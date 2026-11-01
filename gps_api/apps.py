
# داخل ملف gps_api/apps.py (أو اسم تطبيقك)
import os
from django.apps import AppConfig
from django.conf import settings
import firebase_admin
from firebase_admin import credentials

class GpsApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gps_api'

    def ready(self):
        # الكود الخاص بك يوضع هنا ليعمل تلقائياً عند تشغيل السيرفر
        BASE_DIR = settings.BASE_DIR
        cred_path = os.path.join(BASE_DIR, 'firebase-credentials.json')

        if not firebase_admin._apps:
            if os.path.exists(cred_path): # تأمين إضافي للتأكد من وجود الملف
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                print("🔥 تم تهيئة Firebase Admin SDK بنجاح!")
            else:
                print(f"⚠️ تحذير: ملف إعدادات Firebase غير موجود في المسار: {cred_path}")
