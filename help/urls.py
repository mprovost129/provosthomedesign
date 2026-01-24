from django.urls import path
from . import views

app_name = 'help'

urlpatterns = [
    path('', views.help_center, name='help_center'),
    path('faq/', views.faq_list, name='faq_list'),
    path('category/<slug:slug>/', views.category_detail, name='category_detail'),
    path('article/<slug:slug>/', views.article_detail, name='article_detail'),
]
