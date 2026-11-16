import logging
from django.utils.html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .constants import session_label

logger = logging.getLogger(__name__)


def send_payment_confirmation(payment):
    if not payment.receipt_available:
        return False
    subject = f"{'[TEST] ' if payment.mode == 'test' else ''}Payment Successful - {payment.department_display} Dues ({session_label(payment.session_start)})"
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@stechpay.local")
    amount_line = f"NGN {payment.amount:,.2f}"

    text_body = (
        f"Dear {payment.full_name},\n\n"
        f"Your departmental dues payment was successful.\n\n"
        f"Receipt No.:   {payment.receipt_id}\n"
        f"Name:          {payment.full_name}\n"
        f"Matric No.:    {payment.matric_number}\n"
        f"Department:    {payment.department_display}\n"
        f"Level:         {payment.level} Level\n"
        f"Session:       {session_label(payment.session_start)}\n"
        f"Amount:        {amount_line}\n"
        f"Reference:     {payment.reference}\n\n"
        f"You can download your PDF receipt on the portal.\n\n"
        f"Regards,\n{settings.INSTITUTION_NAME}"
    )
    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;border:1px solid #e5eef5;border-radius:8px;overflow:hidden">
      <div style="background:#0f4c81;color:#fff;padding:20px;text-align:center">
        <h2 style="margin:0">{escape(settings.INSTITUTION_NAME)}</h2>
        <p style="margin:4px 0 0">Payment Confirmation</p>
      </div>
      <div style="padding:24px">
        <p>Dear <b>{escape(payment.full_name)}</b>,</p>
        <p>Your departmental dues payment was <b style="color:#0a7d33">successful</b>.</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <tr><td style="padding:6px;background:#eef4fa"><b>Receipt No.</b></td><td style="padding:6px">{escape(payment.receipt_id)}</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Matric Number</b></td><td style="padding:6px">{escape(payment.matric_number)}</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Department</b></td><td style="padding:6px">{escape(payment.department_display)}</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Level</b></td><td style="padding:6px">{escape(payment.level)} Level</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Session</b></td><td style="padding:6px">{escape(session_label(payment.session_start))}</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Amount</b></td><td style="padding:6px">{escape(amount_line)}</td></tr>
          <tr><td style="padding:6px;background:#eef4fa"><b>Reference</b></td><td style="padding:6px">{escape(payment.reference)}</td></tr>
        </table>
        <p style="margin-top:16px">Download your PDF receipt from the portal and keep it safe.</p>
        <p>Regards,<br>{escape(settings.INSTITUTION_NAME)}</p>
      </div>
    </div>
    """

    try:
        message = EmailMultiAlternatives(subject, text_body, from_email, [payment.email])
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Failed to send confirmation email for %s", payment.reference)
        return False
