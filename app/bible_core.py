"""Bible validation, canonical ordering, reference search, and slide pagination."""
import re
import unicodedata

BOOKS = ['Genesis','Exodus','Leviticus','Numbers','Deuteronomy','Joshua','Judges','Ruth',
         '1 Samuel','2 Samuel','1 Kings','2 Kings','1 Chronicles','2 Chronicles','Ezra','Nehemiah','Esther',
         'Job','Psalms','Proverbs','Ecclesiastes','Song of Solomon','Isaiah','Jeremiah','Lamentations',
         'Ezekiel','Daniel','Hosea','Joel','Amos','Obadiah','Jonah','Micah','Nahum','Habakkuk','Zephaniah',
         'Haggai','Zechariah','Malachi','Matthew','Mark','Luke','John','Acts','Romans',
         '1 Corinthians','2 Corinthians','Galatians','Ephesians','Philippians','Colossians',
         '1 Thessalonians','2 Thessalonians','1 Timothy','2 Timothy','Titus','Philemon','Hebrews',
         'James','1 Peter','2 Peter','1 John','2 John','3 John','Jude','Revelation']
SHORT = ['Gen','Exo','Lev','Num','Deut','Josh','Judg','Ruth','1 Sam','2 Sam','1 Kings','2 Kings',
         '1 Chr','2 Chr','Ezra','Neh','Est','Job','Ps','Prov','Eccl','Song','Isa','Jer','Lam','Ezek',
         'Dan','Hos','Joel','Amos','Obad','Jon','Mic','Nah','Hab','Zeph','Hag','Zech','Mal','Matt',
         'Mk','Lk','Jn','Acts','Rom','1 Cor','2 Cor','Gal','Eph','Phil','Col','1 Thess','2 Thess',
         '1 Tim','2 Tim','Titus','Phlm','Heb','Jas','1 Pet','2 Pet','1 Jn','2 Jn','3 Jn','Jude','Rev']

def key(value):
    return re.sub(r'[\s.]+','',unicodedata.normalize('NFKC', str(value)).casefold())

ALIASES = {key(book):book for book in BOOKS}
ALIASES.update({key(short):book for short,book in zip(SHORT,BOOKS)})
ALIASES.update({'psalm':'Psalms','psa':'Psalms','psal':'Psalms','songofsongs':'Song of Solomon',
                'sos':'Song of Solomon','revelations':'Revelation','mt':'Matthew','mr':'Mark',
                'joh':'John','1sa':'1 Samuel','2sa':'2 Samuel','1ki':'1 Kings','2ki':'2 Kings'})

def canonical_book(value):
    text = str(value).strip()
    return ALIASES.get(key(text), text)

def normalize_rows(rows):
    clean, seen = [], {}
    for index,row in enumerate(rows,2):
        row = {str(k).strip().casefold():v for k,v in row.items() if k is not None}
        if not {'book','chapter','verse','text'}.issubset(row):
            raise ValueError('CSV requires book,chapter,verse,text columns.')
        book = canonical_book(row['book'] or '')
        text = str(row['text'] or '').strip()
        try: chapter,verse = int(str(row['chapter']).strip()),int(str(row['verse']).strip())
        except (ValueError,TypeError): raise ValueError(f'Row {index}: chapter and verse must be positive whole numbers.')
        if not book or not text or chapter < 1 or verse < 1:
            raise ValueError(f'Row {index}: book, text, and positive chapter/verse are required.')
        location = (book,chapter,verse)
        if location in seen:
            if seen[location] != text: raise ValueError(f'Row {index}: conflicting duplicate for {book} {chapter}:{verse}.')
            continue
        seen[location] = text
        clean.append({'book':book,'chapter':str(chapter),'verse':str(verse),'text':text})
    if not clean: raise ValueError('The Bible file contains no verses.')
    order = {book:i for i,book in enumerate(BOOKS)}
    extras = list(dict.fromkeys(row['book'] for row in clean if row['book'] not in order))
    order.update({book:len(BOOKS)+i for i,book in enumerate(extras)})
    clean.sort(key=lambda row:(order[row['book']],int(row['chapter']),int(row['verse'])))
    return clean

def search_rows(rows, query):
    query = query.strip()
    reference = re.fullmatch(r'(.+?)\s*(\d+)\s*(?::\s*(\d+)\s*(?:[-–]\s*(\d+))?)?',query)
    if reference:
        book = canonical_book(reference[1])
        chapter = int(reference[2]); first = int(reference[3]) if reference[3] else None
        last = int(reference[4]) if reference[4] else first
        if first is not None and last < first: return []
        return [row for row in rows if key(row['book']) == key(book) and int(row['chapter']) == chapter
                and (first is None or first <= int(row['verse']) <= last)]
    needle = query.casefold()
    return [row for row in rows if needle in ' '.join(row.values()).casefold()]

def passage_reference(rows):
    if not rows:return ''
    first=rows[0]
    if any((r['book'],r['chapter'])!=(first['book'],first['chapter']) for r in rows):return ''
    if any(int(b['verse'])!=int(a['verse'])+1 for a,b in zip(rows,rows[1:])):return ''
    label=f"{first['book']} {first['chapter']}:{first['verse']}"
    if len(rows)>1:label+='-'+str(rows[-1]['verse'])
    return label


def verse_slide(row, version):
    reference = f"{row['book']} {row['chapter']}:{row['verse']} ({version})"
    return {'kind':'verse','body':row['text'],'reference':reference,'verse_number':str(row['verse']),
            'passage_reference':passage_reference([row]),
            'text':row['text']+'\n\n'+reference,'title':reference}

def paginate(rows, version, max_verses=1, max_chars=0):
    """Preserve every word; never merge verses across book/chapter boundaries."""
    limit = max_chars or 620
    groups, group, chars = [], [], 0
    for row in rows:
        same_chapter = not group or (group[-1]['book'],group[-1]['chapter']) == (row['book'],row['chapter'])
        consecutive = not group or int(row['verse']) == int(group[-1]['verse'])+1
        if group and (len(group)>=max_verses or chars+len(row['text'])>limit or not same_chapter or not consecutive):
            groups.append(group); group=[]; chars=0
        group.append(row); chars+=len(row['text'])
    if group: groups.append(group)
    slides = []
    for group in groups:
        ref = f"{group[0]['book']} {group[0]['chapter']}:{group[0]['verse']}"
        if len(group)>1: ref += '–'+group[-1]['verse']
        ref += f' ({version})'
        text = '\n'.join((str(row['verse'])+' ' if len(group)>1 else '')+row['text'] for row in group)
        parts, words = [], []
        for word in text.split():
            if words and len(' '.join(words))+len(word)+1>limit:
                parts.append(' '.join(words)); words=[]
            words.append(word)
        if words: parts.append(' '.join(words))
        # Preserve explicit verse line breaks when no pagination is needed.
        if len(parts)==1: parts=[text]
        for i,body in enumerate(parts,1):
            title = ref + (f' • {i}/{len(parts)}' if len(parts)>1 else '')
            slides.append({'kind':'verse','body':body,'reference':ref,'text':body+'\n\n'+ref,'title':title,
                           'verse_number':str(group[0]['verse']) if len(group)==1 else '',
                           'passage_reference':passage_reference(group)})
    return slides
