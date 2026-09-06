import io

from django.conf import settings
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND = colors.HexColor("#0f4c81")
LIGHT = colors.HexColor("#eef4fa")


def generate_receipt_pdf(payment):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        title=f"Payment Receipt {payment.reference}",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("BrandTitle", parent=styles["Title"], textColor=BRAND, fontSize=22)
    center_style = ParagraphStyle("Center", parent=styles["Normal"], alignment=1)
    meta_sub = ParagraphStyle("MetaSub", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#555555"))

    story = [
        Paragraph(settings.INSTITUTION_NAME, title_style),
        Spacer(1, 2),
        Paragraph("Bursary & Departmental Payments Portal", center_style),
        Spacer(1, 14),
        HRFlowable(width="100%", thickness=1.5, color=BRAND),
        Spacer(1, 10),
        Paragraph("<b>OFFICIAL PAYMENT RECEIPT</b>", ParagraphStyle("ReceiptTitle", parent=center_style, fontSize=13)),
        Spacer(1, 12),
    ]

    rows = [
        ["Receipt No.", str(payment.receipt_id)],
        ["Reference", payment.reference],
        ["Date Paid", payment.paid_at.strftime("%d %b %Y - %H:%M") if payment.paid_at else "-"],
        ["Student Name", payment.full_name],
        ["Matric Number", payment.matric_number],
        ["Department", payment.department_display],
        ["Level", f"{payment.level} Level"],
        ["Session", f"{payment.session_start}/{str(payment.session_start + 1)[2:]}"],
        ["Description", "Departmental Dues"],
        ["Amount Paid", f"NGN {payment.amount:,.2f}"],
    ]
    table = Table(rows, colWidths=[45 * mm, 100 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (0, -1), LIGHT),
                ("TEXTCOLOR", (0, 0), (0, -1), BRAND),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d6e3")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 16))

    status_table = Table(
        [["STATUS: PAID SUCCESSFULLY"]],
        colWidths=[145 * mm],
        rowHeights=[11 * mm],
    )
    status_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(status_table)
    story.append(Spacer(1, 16))

    # QR Code verification widget
    verify_url = f"{settings.FRONTEND_URL}/verify?code={payment.receipt_id}"
    qr_widget = QrCodeWidget(verify_url)
    bounds = qr_widget.getBounds()
    qw = bounds[2] - bounds[0]
    qh = bounds[3] - bounds[1]
    qr_drawing = Drawing(42 * mm, 42 * mm, transform=[42 * mm / qw, 0, 0, 42 * mm / qh, 0, 0])
    qr_drawing.add(qr_widget)

    verification_info = [
        [
            qr_drawing,
            [
                Paragraph("<b>Scan to Verify Receipt Authenticity</b>", ParagraphStyle("QRHead", parent=styles["Normal"], fontSize=9, textColor=BRAND)),
                Spacer(1, 2 * mm),
                Paragraph("Clearance officers and staff can scan this secure QR code using any phone camera to verify validity against official bursary records.", meta_sub),
                Spacer(1, 2 * mm),
                Paragraph(f"Verification URL: {verify_url}", meta_sub),
            ],
        ]
    ]
    verify_table = Table(verification_info, colWidths=[46 * mm, 99 * mm])
    verify_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c9d6e3")),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(verify_table)
    story.append(Spacer(1, 16))

    story.append(
        Paragraph(
            "This receipt was electronically generated and signed. Valid without physical stamp. "
            "Retain this receipt for departmental and faculty clearance.",
            ParagraphStyle("Footer", parent=center_style, fontSize=7.5, textColor=colors.grey),
        )
    )

    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

