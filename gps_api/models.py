from django.db import models
from django.contrib.auth.models import User  # استيراد موديل المستخدمين الافتراضي لـ Django
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.db.models import Sum
def get_default_expiration():
    return timezone.now() + timedelta(days=180)



class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    mobile_num = models.CharField(max_length=20, unique=True)
    otp_code = models.CharField(max_length=6, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)

    def is_otp_valid(self):
        # صلاحية الكود 10 دقائق مثلاً
        if not self.otp_created_at:
            return False
        return timezone.now() <= self.otp_created_at + timedelta(minutes=10)

    def __str__(self):
        return f"{self.user.username} - {self.mobile_num}"

class GPSDevice(models.Model):
    # ربط الجهاز بالمستخدم الحالي (المستخدم يمكنه امتلاك عدة أجهزة)
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE,        # حذف الأجهزة تلقائياً في حال حذف حساب المستخدم لحماية الخصوصية
        related_name="gps_devices"       # يتيح جلب أجهزة المستخدم عبر user.gps_devices.all()
    )
    name = models.CharField(max_length=100)
    imei = models.CharField(max_length=15, unique=True)
    traccar_id = models.IntegerField(unique=True)  # لتخزين الـ ID القادم من Traccar
    model = models.CharField(max_length=50, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    expiration_date = models.DateTimeField(default=get_default_expiration, verbose_name="expiration date")
    pin_code = models.CharField(max_length=128, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    @property
    def days_left(self):
        if self.expiration_date:
            now = timezone.now()
            if self.expiration_date > now:
                # طرح التواريخ يعطي كائن timedelta، نأخذ منه الأيام .days
                return (self.expiration_date - now).days
            return 0  # إذا انتهت الصلاحية يعيد 0
        return None  # في حال لم يكن هناك تاريخ أصلاً
    def __str__(self):
        return f"{self.name} - ({self.user.username})"





# 1. جدول إعدادات تنبيه السرعة الزائدة
class SpeedSettings(models.Model):
    device = models.OneToOneField(GPSDevice, on_delete=models.CASCADE, related_name="speed_setting")
    max_speed = models.FloatField(default=120.0) # كم/ساعة
    is_active = models.BooleanField(default=True)  # تفعيل أو إيقاف التنبيه
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"سرعة: {self.device.name} ({self.max_speed} كم/س)"

# 2. جدول إعدادات تنبيه الاهتزاز والصدمات
class VibrationSettings(models.Model):
    device = models.OneToOneField(GPSDevice, on_delete=models.CASCADE, related_name="vibration_setting")
    max_vibration = models.IntegerField(default=5) # مستوى الحساسية من 1 إلى 10 مثلاً
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"اهتزاز: {self.device.name} (مستوى {self.max_vibration})"

# 3. جدول إعدادات السياج والنطاق الجغرافي
from django.db import models

class GeofenceSettings(models.Model):
    # نحدد الخيارات المتاحة لنوع النطاق
    GEOFENCE_TYPES = [
        ('circle', 'Circle'),
        ('polygon', 'Rectangle'), # أو rectangle
    ]

    device = models.OneToOneField(GPSDevice, on_delete=models.CASCADE, related_name='geofence_settings') # أو ForeignKey حسب منطق مشروعك
    is_active = models.BooleanField(default=True)
    
    # 1. تحديد نوع النطاق
    geofence_type = models.CharField(max_length=10, choices=GEOFENCE_TYPES, default='circle')
    
    # 2. إحداثيات الدائرة (المركز والقطر)
    center_latitude = models.FloatField(null=True, blank=True)
    center_longitude = models.FloatField(null=True, blank=True)
    max_radius = models.FloatField(null=True, blank=True) # القطر الحالي
    
    # 3. إحداثيات المستطيل (نقطتان تشكلان الزوايا المتقاطعة للمستطيل)
    # الزاوية الشمالية الشرقية (North-East)
    ne_latitude = models.FloatField(null=True, blank=True)
    ne_longitude = models.FloatField(null=True, blank=True)
    # الزاوية الجنوبية الغربية (South-West)
    sw_latitude = models.FloatField(null=True, blank=True)
    sw_longitude = models.FloatField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
   



class EmergencyNumbers(models.Model):
    device = models.OneToOneField(
        GPSDevice, 
        on_delete=models.CASCADE, 
        related_name="emergency_numbers",
        verbose_name="الجهاز"
    )
    # تخصيص 3 حقول مستقلة لأرقام الطوارئ
    sos_number_1 = models.CharField(max_length=20, blank=True, null=True, verbose_name="رقم الطوارئ الأول")
    sos_number_2 = models.CharField(max_length=20, blank=True, null=True, verbose_name="رقم الطوارئ الثاني")
    sos_number_3 = models.CharField(max_length=20, blank=True, null=True, verbose_name="رقم الطوارئ الثالث")
    
    updated_at = models.DateTimeField(auto_now=True, verbose_name="تاريخ التحديث")

    class Meta:
        verbose_name = "أرقام الطوارئ للجهاز"
        verbose_name_plural = "أرقام الطوارئ للأجهزة"

    def __str__(self):
        return f"أرقام طوارئ: {self.device.name}"

class OilChangeSettings(models.Model):
    """إعدادات تذكير تغيير الزيت لكل جهاز"""
    device = models.OneToOneField(GPSDevice, on_delete=models.CASCADE, related_name="oil_setting")
    oil_interval_km = models.FloatField(default=5000.0, verbose_name="المسافة الدورية لتغيير الزيت (كم)")
    warn_before_km = models.FloatField(default=500.0, verbose_name="مسافة التحذير المسبق (كم)")
    last_change_odometer = models.FloatField(default=0.0, verbose_name="قراءة العداد عند آخر تغيير (كم)")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"إعدادات زيت: {self.device.name}"


class OilChangeLog(models.Model):
    """سجل توثيق عمليات تغيير الزيت الفعلي"""
    device = models.ForeignKey(GPSDevice, on_delete=models.CASCADE, related_name="oil_logs")
    oil_type = models.CharField(max_length=100, verbose_name="نوع الزيت / الشركة المصنعة")
    filter_changed = models.BooleanField(default=False, verbose_name="تم تغيير المصفاة (الفلتر)")
    current_odometer = models.FloatField(verbose_name="قراءة العداد الحالية أثناء التغيير (كم)")
    notes = models.TextField(blank=True, null=True, verbose_name="ملاحظات إضافية")
    changed_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ ووقت العملية")

    def __str__(self):
        return f"تغيير زيت لـ {self.device.name} عند {self.current_odometer} كم"



class UserDeviceToken(models.Model):
    # ربط الهاتف بالمستخدم المسجل في النظام
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="fcm_tokens")
    # الرمز الفريد للجهاز القادم من الـ Firebase بمشروع الموبايل
    fcm_token = models.CharField(max_length=500, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - Token"


from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class GPSAlarmLog(models.Model):
    ALARM_TYPES = [
        ('sos', 'نداء استغاثة طوارئ'),
        ('overspeed', 'تجاوز السرعة'),
        ('geofence', 'خروج/دخول النطاق'),
        ('vibration', 'اهتزاز أو تلاعب'),
    ]

    device = models.ForeignKey('GPSDevice', on_delete=models.CASCADE, related_name='alarm_logs')
    event_type = models.CharField(max_length=50, choices=ALARM_TYPES)
    title = models.CharField(max_length=200)
    body = models.TextField()
    
    # حقول إضافية لتحديد حالة الـ SOS
    called_sos_number = models.CharField(max_length=20, blank=True, null=True, help_text="رقم الطوارئ الذي تم الاتصال به حالياً")
    current_speed = models.FloatField(default=0.0)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at'] # عرض الأحدث أولاً

    def __str__(self):
        return f"{self.device.name} - {self.get_event_type_display()} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"




class GPSTrip(models.Model):
    device = models.ForeignKey(GPSDevice, on_delete=models.CASCADE, related_name='trips')
    
    # تفاصيل النقطة الأولى والأخيرة من Traccar
    start_lat = models.FloatField()
    start_lon = models.FloatField()
    end_lat = models.FloatField()
    end_lon = models.FloatField()
    
    # التوقيت والمسافة المحسوبة من سيرفر Traccar
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    distance = models.FloatField(help_text="المسافة بالكيلومتر")
    duration = models.IntegerField(help_text="مدة الرحلة بالثواني")

    max_speed = models.FloatField(default=0.0, help_text="أقصى سرعة في الرحلة (كم/س)")  # <-- أضف هذا الحقل
  
    # حقل لمنع تكرار سحب نفس الرحلة من Traccar
    # Traccar لا يملك ID ثابت للرحلة، لذا سنقوم بتركيب بصمة فريدة (unique hash) تجمع بين وقت البداية والنهاية والمعرف
    trip_signature = models.CharField(max_length=100, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trip for {self.device.name} [{self.distance} km]"




from django.db import models
from django.utils import timezone
from django.db.models import Sum, Max
from django.db.models.signals import post_save
from django.dispatch import receiver

class DeviceOdometer(models.Model):
    device = models.OneToOneField(
        GPSDevice, 
        on_delete=models.CASCADE, 
        related_name='odometer'
    )
    archived_distance = models.FloatField(default=0.0, help_text="المسافة المجمعة من الرحلات التي تم تصفيرها")
    archived_duration = models.IntegerField(default=0, help_text="المدة المجمعة بالثواني")
    max_speed_record = models.FloatField(default=0.0, help_text="الرقم القياسي لأقصى سرعة منذ التفعيل")
    last_reset_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def get_current_trip_stats(self):
        """حساب إحصائيات الرحلة الحالية (المسافة، المدة، متوسط السرعة، وأقصى سرعة حالية)"""
        trips = self.device.trips.filter(end_time__gt=self.last_reset_at)
        agg = trips.aggregate(
            dist=Sum('distance'),
            dur=Sum('duration'),
            curr_max_speed=Max('max_speed')
        )
        curr_dist = round(agg['dist'] or 0.0, 2)
        curr_dur = agg['dur'] or 0
        curr_avg_speed = round((curr_dist / (curr_dur / 3600)), 1) if curr_dur > 0 else 0.0
        curr_max = round(agg['curr_max_speed'] or 0.0, 1)
        
        return {
            "distance": curr_dist,
            "duration": curr_dur,
            "avg_speed": curr_avg_speed,
            "max_speed": curr_max,
        }

    def get_live_total_stats(self):
        """حساب الإحصائيات الكلية اللحظية بدون ازدواجية"""
        current = self.get_current_trip_stats()
        live_total_dist = round(self.archived_distance + current["distance"], 2)
        live_total_dur = self.archived_duration + current["duration"]
        live_total_avg_speed = round((live_total_dist / (live_total_dur / 3600)), 1) if live_total_dur > 0 else 0.0

        return {
            "total_distance": live_total_dist,
            "total_avg_speed": live_total_avg_speed,
            "max_speed_record": round(self.max_speed_record, 1),
        }

    def __str__(self):
        return f"Odometer for {self.device.name}"


# ==================== SIGNALS ====================

@receiver(post_save, sender=GPSDevice)
def create_device_odometer(sender, instance, created, **kwargs):
    """1. إنشاء سجل عداد تلقائياً عند إضافة جهاز جديد"""
    if created:
        DeviceOdometer.objects.get_or_create(device=instance)


@receiver(post_save, sender=GPSTrip)
def update_odometer_max_speed(sender, instance, created, **kwargs):
    """2. تحديث الرقم القياسي للسرعة تلقائياً عند وصول أي رحلة جديدة من Traccar"""
    if created and instance.max_speed > 0:
        odometer, _ = DeviceOdometer.objects.get_or_create(device=instance.device)
        if instance.max_speed > odometer.max_speed_record:
            odometer.max_speed_record = round(instance.max_speed, 1)
            odometer.save(update_fields=['max_speed_record', 'updated_at'])
