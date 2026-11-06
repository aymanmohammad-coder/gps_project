# utils.py

import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def send_textbee_sms(recipients, message):
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
