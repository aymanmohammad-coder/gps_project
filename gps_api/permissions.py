from rest_framework import permissions

class IsDeviceOwner(permissions.BasePermission):
    """
    السماح فقط لصاحب الجهاز بالوصول إليه وتعديله أو حذفه.
    """
    def has_object_permission(self, request, view, obj):
        return obj.user == request.user

class IsAlarmLogOwner(permissions.BasePermission):
    """
    يسمح فقط للمستخدم الذي يملك جهاز الـ GPS المرتبط بالسجل بحذفه.
    """
    def has_object_permission(self, request, view, obj):
        # التأكد من أن صاحب الجهاز هو نفسه المستخدم الحالي الذي أرسل الطلب
        return obj.device.user == request.user