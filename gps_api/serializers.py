from rest_framework import serializers
from .models import GPSDevice,GPSAlarmLog

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