"""Resolution-independent slide drawing; layout never grows to fit slide text."""
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QTextDocument, QTextOption, QTextCursor, QTextCharFormat, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy, QLabel

PROFILE_DEFAULTS = {
    'bible_theme':'Global','song_theme':'Global',
    'bible_background_image':'','song_background_image':'',
    'bible_header_enabled':False,'bible_header_text':'RCCG JESUS THE CHAMPION',
    'bible_header_color':'#f1cf83','bible_header_size':22,
    'bible_reference_enabled':True,'bible_reference_position':'Bottom',
    'bible_reference_color':'#f1cf83','bible_reference_size':24,
    'bible_footer_enabled':True,'bible_footer_text':'',
    'bible_footer_color':'#eacb79','bible_footer_size':16,
}


def slide_parts(item):
    body = item.get('body', item.get('text', ''))
    reference = item.get('reference', '')
    if item.get('kind') == 'verse' and not reference:
        parts = body.rsplit('\n\n', 1)
        if len(parts) == 2:
            body, reference = parts
    if item.get('kind')=='verse' and item.get('verse_number'):
        body=str(item['verse_number'])+' '+body
    return body, reference


class SlideCanvas(QWidget):
    def __init__(self, item, settings, parent=None):
        super().__init__(parent)
        self.item, self.settings = item, dict(settings)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        source = item.get('path') if item.get('kind') == 'image' else settings.get('background_image', '')
        self.image = QPixmap(str(source)) if source else QPixmap()
        self.body_rect = QRectF()
        self.reference_rect = QRectF()
        self.passage_rect = QRectF()
        self.rendered_font = 0
        self.document_height = 0
        self.header_rect = QRectF()
        self.document_cache = {}

    def sizeHint(self): return QSize(640, 360)
    def minimumSizeHint(self): return QSize(0, 0)

    def document(self, text, width, height, pixels, alignment):
        cache_key = (text,width,height,pixels,int(alignment))
        if cache_key in self.document_cache: return self.document_cache[cache_key]
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        option = QTextOption()
        option.setAlignment(alignment)
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        font = QFont(self.settings.get('font_family', 'Arial'))
        font.setBold(True); font.setItalic(False)
        low, high = 1, max(1, int(pixels))
        best = 1
        while low <= high:
            middle = (low + high) // 2
            font.setPixelSize(middle); doc.setDefaultFont(font)
            doc.setPlainText(text); doc.setTextWidth(max(1, width))
            if doc.size().height() <= max(1, height): best = middle; low = middle + 1
            else: high = middle - 1
        font.setPixelSize(best); doc.setDefaultFont(font)
        doc.setPlainText(text); doc.setTextWidth(max(1, width))
        if len(self.document_cache)>8:self.document_cache.clear()
        self.document_cache[cache_key]=(doc,best)
        return doc, best

    def draw_text(self, painter, text, rect, pixels, alignment, color):
        doc, fitted = self.document(text, rect.width(), rect.height(), pixels, alignment)
        # Explicit text colour avoids inherited desktop widget styles.
        doc.setDefaultStyleSheet('body { color: ' + color + '; }')
        cursor = QTextCursor(doc); cursor.select(QTextCursor.Document)
        fmt = QTextCharFormat(); fmt.setForeground(QColor(color)); cursor.mergeCharFormat(fmt)
        y = rect.top() + max(0, (rect.height() - doc.size().height()) / 2)
        painter.save(); painter.setClipRect(rect)
        painter.translate(rect.left(), y)
        doc.drawContents(painter, QRectF(0, 0, rect.width(), rect.height()))
        painter.restore()
        return fitted, doc.size().height()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        background=QColor(self.settings.get('background', '#080d1a'))
        end=self.settings.get('gradient_end','')
        if end:
            gradient=QLinearGradient(0,0,self.width(),self.height());gradient.setColorAt(0,background);gradient.setColorAt(1,QColor(end))
            painter.fillRect(self.rect(),gradient)
        else:painter.fillRect(self.rect(),background)
        if not self.image.isNull():
            mode = Qt.KeepAspectRatio if self.item.get('kind') == 'image' and self.settings.get('image_fit') != 'cover' else Qt.KeepAspectRatioByExpanding
            scaled = self.image.scaled(self.size(), mode, Qt.SmoothTransformation)
            painter.drawPixmap((self.width()-scaled.width())//2, (self.height()-scaled.height())//2, scaled)
            if self.item.get('kind') != 'image': painter.fillRect(self.rect(), QColor(0, 0, 0, 115))
        if self.item.get('kind') == 'image': return
        body, reference = slide_parts(self.item)
        is_bible=self.item.get('kind')=='verse'
        passage=self.item.get('passage_reference','') if is_bible else ''
        header=self.settings.get('bible_header_text','') if is_bible and self.settings.get('bible_header_enabled') else ''
        if is_bible and not self.settings.get('bible_reference_enabled',True): reference=''
        margin = min(self.width(), self.height()) * .065
        area = QRectF(margin, margin, max(1, self.width()-2*margin), max(1, self.height()-2*margin))
        reference_height = min(area.height()*.17, self.height()*.12) if reference else 0
        gap = self.height()*.025 if reference else 0
        header_height=area.height()*.1 if header else 0
        passage_height=area.height()*.11 if passage else 0
        self.passage_rect=QRectF(area.left(),area.top(),area.width(),passage_height)
        header_top=area.top()+passage_height+(self.height()*.02 if passage else 0)
        self.header_rect=QRectF(area.left(),header_top,area.width(),header_height)
        top=header_top+header_height+(gap if header else 0)
        reference_top=is_bible and self.settings.get('bible_reference_position')=='Top'
        if reference_top:
            self.reference_rect=QRectF(area.left(),top,area.width(),reference_height)
            top+=reference_height+gap
            self.body_rect=QRectF(area.left(),top,area.width(),max(1,area.bottom()-top))
        else:
            self.reference_rect = QRectF(area.left(), area.bottom()-reference_height, area.width(), reference_height)
            self.body_rect = QRectF(area.left(),top,area.width(),max(1,self.reference_rect.top()-gap-top))
        align = {'Left':Qt.AlignLeft,'Center':Qt.AlignHCenter,'Right':Qt.AlignRight}.get(self.settings.get('text_align'), Qt.AlignHCenter)
        pixels = float(self.settings.get('font_size', 52)) * 4/3 * self.width()/1280
        color = self.settings.get('text_color', '#ffffff')
        self.rendered_font, self.document_height = self.draw_text(painter, body, self.body_rect, pixels, align, color)
        if passage:
            self.draw_text(painter,passage,self.passage_rect,float(self.settings.get('bible_reference_size',24))*4/3*self.width()/1280,
                           Qt.AlignHCenter,self.settings.get('bible_reference_color','#f1cf83'))
        if header:
            self.draw_text(painter,header,self.header_rect,float(self.settings.get('bible_header_size',22))*4/3*self.width()/1280,
                           Qt.AlignHCenter,self.settings.get('bible_header_color','#f1cf83'))
        if reference:
            self.draw_text(painter, reference, self.reference_rect,float(self.settings.get('bible_reference_size',24))*4/3*self.width()/1280,
                           Qt.AlignHCenter,self.settings.get('bible_reference_color','#f1cf83'))


class AspectFrame(QWidget):
    """Letterbox an embedded view without changing the main window minimum size."""
    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.view = view; view.setParent(self)
        self.setMinimumSize(100, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet('background:#050911;')

    def resizeEvent(self, event):
        self.layout_view()
        super().resizeEvent(event)

    def layout_view(self):
        if self.view.parent() is not self:return
        width = min(self.width(), int(self.height()*16/9))
        height = min(self.height(), int(width*9/16))
        self.view.setGeometry((self.width()-width)//2, (self.height()-height)//2, width, height)


class MonitorLabel(QLabel):
    """Rescale snapshots on every resize, including between refresh ticks."""
    def paintEvent(self, event):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            super().paintEvent(event); return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor('#050911'))
        image = pixmap.scaled(self.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        painter.drawPixmap((self.width()-image.width())//2,(self.height()-image.height())//2,image)
