from django.db import models
from django.contrib.auth.models import User  # استيراد موديل المستخدمين الافتراضي لـ Django

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
    created_at = models.DateTimeField(auto_now_add=True)

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
class GeofenceSettings(models.Model):
    device = models.OneToOneField(GPSDevice, on_delete=models.CASCADE, related_name="geofence_setting")
    max_radius = models.FloatField(default=500.0) # المسافة المسموحة بالمتر خارج النطاق
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"سياج: {self.device.name} ({self.max_radius} متر)"




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
