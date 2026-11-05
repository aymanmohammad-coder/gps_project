# utils.py
import requests
from requests.auth import HTTPBasicAuth
import logging

logger = logging.getLogger(__name__)

def send_sms_via_android(phone_number, message_text):
    # 💡 الرابط والمسار الرسمي المعتمد من التوثيق لقنوات الإرسال السحابي للطرف الثالث
    API_URL = "https://api.sms-gate.app/"
    
    # المعاملات الإضافية الموصى بها في التوثيق لتخطي فحص صيغة الرقم الصارمة
    
    params = {"skipPhoneValidation": True}
    
    USERNAME = "BNUPZA"
    PASSWORD = "apmmsfwkcujna6"
    DEVICE_ID = "90xKuJ1Wjne03k_CzZsnn"
    
    # الهيكلية القياسية المطابقة لتوثيق المنصة لترحيل حزمة الرسالة النصية العادية
    payload = {
        "textMessage": {
            "text": message_text         # نص الأمر المرسل للمركبة (monitor123456)
        },
        "deviceId": DEVICE_ID,           # معرف جهاز الأندرويد السحابي
        "phoneNumbers": [phone_number],  # يجب إرسال الرقم داخل مصفوفة (List)
       

        
        "priority": 100          ,         # الأولوية القصوى (تتخطى قيود الـ Rate Limits المدمجة بالتطبيق)
    }
    print(payload)
    try:
        # استخدام نظام التوثيق الأساسي المعتمد في المنصة (Basic Auth)
        auth_credentials = HTTPBasicAuth(USERNAME, PASSWORD)
        print(auth_credentials)
        response = requests.post(
            API_URL, 
            params=params,
            json=payload, 
            auth=auth_credentials, 
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print("--- SMS GATE RESPONSE ---")
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        print("-------------------------")
        
        # يعود السيرفر بكود 200 أو 201 عند قبول معالجة الرسالة بنجاح وتوجيهها لهاتفك
        return response.status_code in [200, 201]
            
    except requests.exceptions.RequestException as e:
        logger.error(f"فشل الاتصال بالسيرفر السحابي لـ SMS Gate: {str(e)}")
        return False
