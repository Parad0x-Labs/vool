"""A local SVG QR image from the already pinned PDF drawing library."""
import base64


def image(link):
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    widget = QrCodeWidget(link, barLevel='M', barWidth=360, barHeight=360)
    drawing = Drawing(360, 360)
    drawing.add(widget)
    svg = renderSVG.drawToString(drawing)
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()
