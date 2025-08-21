from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

def send_contact_email(context: dict, subject: str):
    """
    Sends the internal notification to you, and optionally an auto-ack to the sender.
    context must include: name, email, message (phone optional).
    """
    # Internal notification to you
    html = render_to_string("emails/contact_email.html", context)
    txt  = render_to_string("emails/contact_email.txt", context)
    msg = EmailMultiAlternatives(
        subject=f"{settings.CONTACT_EMAIL_SUBJECT_PREFIX} {subject}",
        body=txt,
        from_email=settings.DEFAULT_FROM_EMAIL,          # your M365 mailbox
        to=settings.CONTACT_TO_EMAILS,                   # supports comma list from env
        reply_to=[context.get("email")] if context.get("email") else None, # type: ignore
    )
    msg.attach_alternative(html, "text/html")
    msg.send(fail_silently=False)

def send_contact_ack(context: dict):
    """Optional polite confirmation to the visitor."""
    if not context.get("email"):
        return
    html = render_to_string("emails/contact_ack.html", context)
    txt  = render_to_string("emails/contact_ack.txt", context)
    ack = EmailMultiAlternatives(
        subject="Thanks for reaching out — Provost Home Design",
        body=txt,
        from_email=getattr(settings, "AUTO_ACK_FROM_EMAIL", settings.DEFAULT_FROM_EMAIL),
        to=[context["email"]],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    ack.attach_alternative(html, "text/html")
    ack.send(fail_silently=False)
