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
    "Copon_303F": {
        "stop": "stop123456",
        "resume": "resume123456",
        "monitor": "monitor123456",
        "tracker": "tracker123456",
        "shock_on": "shock123456",
        "shock_off": "noshock123456",
        "speed_on": "speed123456 {speed:03d}",      # مثال: speed123456 080
        "speed_off": "nospeed123456",
        "geofence_on": "move123456 {radius:04d}",  # مثال: move123456 0200
        "geofence_off": "nomove123456",
        "sos_set": "admin123456 {phone}",
    },
        "Istartek_VT200_L": {
        "stop": "0000,900,1,1",
        "resume": "0000,900,1,0",
        "monitor": "0000,151,3",                 # تم التحديث بالكود الناجح لنسختك
        "tracker": "0000,151,0",                 # العودة للوضع العادي
        "shock_on": "0000,132,10",
        "shock_off": "0000,132,0",
        "speed_on": "0000,123,{speed}",
        "speed_off": "0000,123,0",
        "geofence_on": "0000,125,1,1,{radius},{lat},{lon}",
        "geofence_off": "0000,127,1",
        "sos_set": "0000,201,{phones}",
        "enable_call_alerts": "0000,204,1,1",
        "enable_sms_alerts": "0000,203,1,1",
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
