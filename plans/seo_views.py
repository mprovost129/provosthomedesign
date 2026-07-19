from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from pages.models import ProjectCaseStudy
from .models import Plans


def image_sitemap(request: HttpRequest) -> HttpResponse:
    entries = []
    plans = Plans.objects.filter(is_available=True).prefetch_related("images")
    for plan in plans:
        images = []
        if plan.main_image:
            images.append({
                "loc": request.build_absolute_uri(plan.main_image.url),
                "title": f"Front elevation of house plan {plan.plan_number}",
            })
        for gallery_image in plan.images.all():
            images.append({
                "loc": request.build_absolute_uri(gallery_image.image.url),
                "title": gallery_image.caption
                or f"{gallery_image.get_kind_display()} for house plan {plan.plan_number}",
            })
        if images:
            entries.append({
                "loc": request.build_absolute_uri(plan.get_absolute_url()),
                "images": images,
            })
    case_studies = ProjectCaseStudy.objects.filter(is_published=True).prefetch_related("images")
    for case_study in case_studies:
        images = []
        if case_study.hero_image:
            images.append({
                "loc": request.build_absolute_uri(case_study.hero_image.url),
                "title": case_study.title,
            })
        images.extend(
            {
                "loc": request.build_absolute_uri(item.image.url),
                "title": item.alt_text,
            }
            for item in case_study.images.all()
        )
        if images:
            entries.append({
                "loc": request.build_absolute_uri(case_study.get_absolute_url()),
                "images": images,
            })
    return render(
        request,
        "plans/image_sitemap.xml",
        {"entries": entries},
        content_type="application/xml",
    )
