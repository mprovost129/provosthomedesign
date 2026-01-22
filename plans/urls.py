from django.urls import path
from . import views

app_name = "plans"

urlpatterns = [
    # ── Staff/admin endpoints (keep BEFORE slug routes) ───────────────────────
    path("admin/quick-create/", views.quick_create_plan, name="quick_create_plan"),
    path("admin/gallery/upload/<int:plan_id>/", views.gallery_upload, name="gallery_upload"),
    path("admin/gallery/delete/<int:image_id>/", views.gallery_delete, name="gallery_delete"),
    path("admin/gallery/make-cover/<int:image_id>/", views.gallery_make_cover, name="gallery_make_cover"),

    # ── Public listing & utilities ────────────────────────────────────────────
    path("", views.plan_list, name="plan_list"),
    path("style/<slug:house_style_slug>/", views.plan_list, name="plan_list_by_style"),
    path("search/", views.search, name="search"),
    path("<int:plan_id>/comment/", views.send_plan_comment, name="send_plan_comment"),
    
    # ── Favorites & Comparison (BEFORE detail route) ─────────────────────────
    path("favorites/", views.favorites_list, name="favorites_list"),
    path("favorite/toggle/<int:plan_id>/", views.toggle_favorite, name="toggle_favorite"),
    path("compare/", views.compare_plans, name="compare_plans"),
    path("compare/toggle/<int:plan_id>/", views.toggle_comparison, name="toggle_comparison"),
    path("compare/clear/", views.clear_comparison_view, name="clear_comparison"),

    # ── Detail (MUST be last so it doesn't swallow others) ───────────────────
    path("<slug:house_style_slug>/<slug:plan_slug>/", views.plan_detail, name="plan_detail"),
]
