from django.urls import path
from . import views

app_name = 'timetracking'

urlpatterns = [
    # Dashboard
    path('', views.time_dashboard, name='time_dashboard'),
    
    # Time Entries
    path('entries/', views.time_entries_list, name='entries_list'),
    path('entry/create/', views.create_time_entry, name='create_entry'),
    path('entry/<int:pk>/edit/', views.edit_time_entry, name='edit_entry'),
    path('entry/<int:pk>/delete/', views.delete_time_entry, name='delete_entry'),
    
    # Timer API
    path('timer/start/', views.start_timer, name='start_timer'),
    path('timer/stop/', views.stop_timer, name='stop_timer'),
    path('timer/status/', views.get_timer_status, name='timer_status'),
    
    # Project View
    path('project/<int:project_id>/', views.project_time_entries, name='project_entries'),
]
