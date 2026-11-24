# utils.py

import requests
from django.conf import settings
import logging



logger = logging.getLogger(__name__)


def format_syrian_phone(phone_number):
    """تنسيق رقم الهاتف ليصبح بصيغة دولية (+963...) صالحة لبوابة SMS"""
    if not phone_number:
        return None
    phone = str(phone_number).strip().replace(" ", "")
    if phone.startswith("09"):
        return "+963" + phone[1:]
    elif phone.startswith("00"):
        return "+" + phone[2:]
    elif not phone.startswith("+"):
        return "+" + phone
    return phone


# قاموس الأكواد الموحد لجميع الأجهزة
DEVICE_SMS_COMMANDS = {
    # داخل DEVICE_SMS_COMMANDS في utils.py

    "Copon_303F": {
        # --- أوامر التهيئة الأولية ---
        "init_begin": "begin123456",
        "init_timezone": "time zone123456 3",           # توقيت دمشق (+3)
        "init_apn": "apn123456 {apn}",                  # مثال: ://syriatel.com أو internet
        "init_ip": "adminip123456 {ip} {port}",         # منفذ Coban في Traccar هو 5001
        "init_gprs": "gprs123456",
        "init_fix": "fix030s***n123456",                # إرسال الموقع كل 30 ثانية
        # --- أوامرك السابقة ---
        "stop": "stop123456",
        "resume": "resume123456",
        "monitor": "monitor123456",
        "tracker": "tracker123456",
        "shock_on": "shock123456",
        "shock_off": "noshock123456",
        "speed_on": "speed123456 {speed:03d}",
        "speed_off": "nospeed123456",
        "geofence_on": "move123456 {radius:04d}",
        "geofence_off": "nomove123456",
        "sos_set": "admin123456 {phone}",
    },
    "Istartek_VT200_L": {
        # --- أوامر التهيئة الأولية ---
        "init_apn": "0000,101,{apn}",                   # ضبط نقطة الوصول APN
        "init_ip": "0000,102,1,{ip},{port},0",          # ربط السيرفر (TCP=0)، منفذ iStartek في Traccar هو 5013 أو 5023
        "init_interval": "0000,103,30,30",              # إرسال البيانات كل 30 ثانية (محرك يعمل/متوقف)
        "init_timezone": "0000,115,180",                # توقيت دمشق (+180 دقيقة)
        # --- أوامرك السابقة ---
        "stop": "0000,900,1,1",
        "resume": "0000,900,1,0",
        "monitor": "0000,151,3",
        "tracker": "0000,151,0",
        "shock_on": "0000,132,10",
        "shock_off": "0000,132,0",
        "speed_on": "0000,123,{speed}",
        "speed_off": "0000,123,0",
        "geofence_on": "0000,125,1,1,{radius},{lat},{lon}",
        "geofence_off": "0000,127,1",
        "sos_set": "0000,201,{phones}",
        "enable_call_alerts_1": "0000,206,1,1,1,22,25,35",
        "enable_call_alerts_2": "0000,206,2,1,1,22,25,35",
        "enable_call_alerts_3": "0000,206,3,1,1,22,25,35",
        "enable_sms_alerts_1": "0000,203,1,1,1,22,25,35",
        "enable_sms_alerts_2": "0000,203,2,1,1,22,25,35",
        "enable_sms_alerts_3": "0000,203,3,1,1,22,25,35",
    }


}


def send_textbee_sms(recipients, message):
    """دالة إرسال رسائل SMS عبر بوابة Textbee"""
    url = "https://api.textbee.dev/api/v1/gateway/send-sms"
    api_key = getattr(settings, 'TEXTBEE_API_KEY', None)
    
    if not api_key:
        logger.error("Textbee API key is not configured in Django settings.")
        return False, "تكوين السيرفر غير مكتمل: مفتاح الـ API مفقود"

    headers = {
        'Content-Type': 'application/json',
        'x-api-key': api_key,
    }
    
    sms_payload = {
        "recipients": recipients,
        "message": message
    }
    
    try:
        response = requests.post(url, json=sms_payload, headers=headers, timeout=15)
        if response.status_code in[200,201,202]:
            try:
                return True, response.json()
            except ValueError:
                return True, response.text
        else:
            logger.warning(f"Textbee rejected request. Status: {response.status_code}, Response: {response.text}")
            return False, f"رفضت البوابة الطلب بكود: {response.status_code}"
    except requests.exceptions.RequestException as e:
        logger.error(f"External Textbee gateway connection failed: {str(e)}")
        return False, f"فشل الاتصال ببوابة الرسائل الخارجية: {str(e)}"


def dispatch_device_command(device, command_key, **kwargs):
    """
    دالة موحدة لجلب الكود المناسب للجهاز، تعبئة المتغيرات، وإرسال الرسالة للشريحة
    :return: tuple (success_bool, response_dict_or_error_str, status_code)
    """
    phone = format_syrian_phone(device.phone_number)
    if not phone:
        return False, "لا يوجد رقم شريحة مسجل لهذا الجهاز.", 400

    model_commands = DEVICE_SMS_COMMANDS.get(device.model)
    if not model_commands:
        return False, f"نوع الجهاز ({device.model}) غير مدعوم.", 400

    raw_command = model_commands.get(command_key)
    if not raw_command:
        return False, f"الأمر ({command_key}) غير مدعوم لهذا النوع من الأجهزة.", 400

    # تعبئة المتغيرات الديناميكية في الكود (مثل السرعة أو القطر أو أرقام SOS)
    try:
        sms_command = raw_command.format(**kwargs) if kwargs else raw_command
    except KeyError as e:
        return False, f"معطيات ناقصة لتكوين الأمر: {str(e)}", 400

    success, result = send_textbee_sms([phone], sms_command)
    if success:
        return True, {
            "sent_to": phone,
            "command": sms_command,
            "gateway_response": result
        }, 200
    else:
        return False, result, 502


def send_textbee_sms_1(recipients, message):
    """
    دالة عامة لإرسال رسائل SMS عبر بوابة Textbee
    :param recipients: قائمة بأرقام الهواتف e.g. ['+963946620316']
    :param message: نص الرسالة المراد إرسالها
    :return: tuple (success_boolean, result_data_or_error_string)
    """
    url = "https://api.textbee.dev/api/v1/gateway/send-sms"
    api_key = getattr(settings, 'TEXTBEE_API_KEY', None)
    
    if not api_key:
        logger.error("Textbee API key is not configured in Django settings.")
        return False, "تكوين السيرفر غير مكتمل: مفتاح الـ API مفقود"

    headers = {
        'Content-Type': 'application/json',
        'x-api-key': api_key,
    }
    
    sms_payload = {
        "recipients": recipients,
        "message": message
    }
    
    try:
        response = requests.post(url, json=sms_payload, headers=headers, timeout=15)
        
        if response.status_code in[200,201,202]:
            try:
                return True, response.json()
            except ValueError:
                return True, response.text
        else:
            logger.warning(f"Textbee rejected request. Status: {response.status_code}, Response: {response.text}")
            return False, f"رفضت البوابة الطلب بكود: {response.status_code}"
            
    except requests.exceptions.RequestException as e:
        logger.error(f"External Textbee gateway connection failed: {str(e)}")
        return False, f"فشل الاتصال ببوابة الرسائل الخارجية: {str(e)}"
