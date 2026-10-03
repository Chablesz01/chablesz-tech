"""Find remembered words without requiring a scripture reference."""
import re
import unicodedata
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QDialog,QVBoxLayout,QHBoxLayout,QLineEdit,QComboBox,QLabel,QListWidget,QPushButton
from bible_core import verse_slide,passage_reference


def find_words(rows,query,mode='All words',book=''):
    query=unicodedata.normalize('NFKC',query).casefold().strip()
    if not query:return []
    words=re.findall(r'\w+',query,flags=re.UNICODE)
    if not words:return []
    patterns=[re.compile(r'(?<!\w)'+re.escape(w)+r'(?!\w)') for w in words]
    phrase=re.compile(r'(?<!\w)'+r'\s+'.join(re.escape(w) for w in query.split())+r'(?!\w)')
    found=[]
    for row in rows:
        if book and row['book']!=book:continue
        text=unicodedata.normalize('NFKC',row['text']).casefold()
        matched=(bool(phrase.search(text)) if mode=='Exact phrase' else
                 any(p.search(text) for p in patterns) if mode=='Any word' else all(p.search(text) for p in patterns))
        if matched:found.append(row)
    return found

class WordFinder(QDialog):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.found=[];self.setWindowTitle('Find Bible words — '+app.version.currentText())
        self.resize(730,570);layout=QVBoxLayout(self)
        hint=QLabel('Type remembered words, choose a result, then Show Live. All words can appear in any order.');hint.setWordWrap(True);layout.addWidget(hint)
        self.query=QLineEdit();self.query.setPlaceholderText('Example: valley shadow death');layout.addWidget(self.query)
        row=QHBoxLayout();layout.addLayout(row)
        self.mode=QComboBox();self.mode.addItems(['All words','Exact phrase','Any word']);row.addWidget(self.mode)
        self.book=QComboBox();self.book.addItem('All books','');self.book.addItems(list(dict.fromkeys(r['book'] for r in app.verses)));row.addWidget(self.book)
        self.results=QListWidget();self.results.setWordWrap(True);self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);layout.addWidget(self.results,1)
        self.count=QLabel('Search the installed '+app.version.currentText()+' text.');layout.addWidget(self.count)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.search)
        self.query.textChanged.connect(lambda:self.timer.start(180));self.query.returnPressed.connect(self.search)
        self.mode.currentTextChanged.connect(self.search);self.book.currentIndexChanged.connect(self.search)
        self.results.itemActivated.connect(lambda _:self.send('live'))
        actions=QHBoxLayout();layout.addLayout(actions)
        self.action_buttons=[]
        for title,action in [('Preview','preview'),('Show Live','live'),('Chapter from this verse','chapter'),('Queue verse','queue')]:
            button=QPushButton(title);button.setAutoDefault(False);button.clicked.connect(lambda _,a=action:self.send(a));actions.addWidget(button);self.action_buttons.append(button)
        self.results.currentRowChanged.connect(self.enable_actions);self.enable_actions(-1);self.query.setFocus()

    def enable_actions(self,row):
        for button in self.action_buttons:button.setEnabled(0<=row<len(self.found))

    def search(self,*_):
        self.timer.stop();book='' if self.book.currentIndex()==0 else self.book.currentText()
        found=find_words(self.app.verses,self.query.text(),self.mode.currentText(),book)
        self.found=found[:500];self.results.clear()
        for row in self.found:
            self.results.addItem(f"{passage_reference([row])} — {row['text']}")
            self.results.item(self.results.count()-1).setToolTip(row['text'])
        if self.found:self.results.setCurrentRow(0)
        self.count.setText(f'{len(found):,} matches'+(' — first 500 shown; add more words to narrow the search.' if len(found)>500 else '') if found else 'No match. Try fewer words or another installed translation.')

    def send(self,action):
        index=self.results.currentRow()
        if not 0<=index<len(self.found):return
        verse=self.found[index];query=passage_reference([verse])
        if action=='chapter':query=f"{verse['book']} {verse['chapter']}"
        self.app.query.setText(query);self.app.search_bible()
        if action=='chapter':
            row=next((i for i,r in enumerate(self.app.matches) if r['verse']==verse['verse']),0)
            self.app.results.setCurrentRow(row)
        if action in ('live','chapter'):self.app.verse_live();self.accept()
        elif action=='queue':self.app.add_verse();self.count.setText('Verse queued; Live is unchanged.')
        else:self.count.setText('Verse prepared in Preview; Live is unchanged.')
