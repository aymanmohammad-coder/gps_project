from rest_framework import serializers
from .models import GPSDevice,GPSAlarmLog,VehicleLicense

class GPSDeviceSerializer(serializers.ModelSerializer):
    # حقل days_left للقراءة فقط لأنه خاصية محسوبة (property)
    days_left = serializers.ReadOnlyField()
    
    class Meta:
        model = GPSDevice
        # يمكنك تحديد الحقول التي تريد السماح بتحديثها أو عرضها
        fields = [
            'id', 'name', 'imei', 'traccar_id', 'model', 
            'phone_number', 'expiration_date', 'pin_code', 
            'created_at', 'days_left'
        ]
        read_only_fields = ['imei', 'traccar_id'] 
        
class GPSAlarmLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = GPSAlarmLog
        fields = '__all__'



class VehicleLicenseSerializer(serializers.ModelSerializer):
    device_id = serializers.PrimaryKeyRelatedField(
        queryset=GPSDevice.objects.all(),
        source='device'
    )
    days_remaining = serializers.IntegerField(read_only=True)
    license_status = serializers.CharField(read_only=True)

    class Meta:
        model = VehicleLicense
        fields = [
            'id',
            'device_id',
            'plate_number',
            'license_expiry_date',
            'days_remaining',
            'license_status',
            'updated_at'
        ]
