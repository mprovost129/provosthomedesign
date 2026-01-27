from django.urls import path
from . import api

urlpatterns = [
    path('timer/status/', api.timer_status, name='timer_status'),
    path('timer/start/', api.timer_start, name='timer_start'),
    path('timer/stop/', api.timer_stop, name='timer_stop'),
    path('timer/reset/', api.timer_reset, name='timer_reset'),
]
