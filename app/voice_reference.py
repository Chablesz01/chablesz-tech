"""Conservative spoken scripture parsing; never invent or fuzzy-map a Bible book."""
from dataclasses import dataclass
import re, unicodedata
from bible_core import BOOKS, ALIASES, key, search_rows
UNITS={w:n for n,w in enumerate('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split())}
TENS={w:n for w,n in zip('twenty thirty forty fifty sixty seventy eighty ninety'.split(),range(20,100,10))}
ORDINAL={'first':'1','second':'2','third':'3'}
@dataclass(frozen=True)
class SpokenReference:
    book:str
    chapter:int
    first:int|None=None
    last:int|None=None
    @property
    def query(self):
        text=f'{self.book} {self.chapter}'
        if self.first is not None:
            text+=':'+str(self.first)
            if self.last!=self.first:text+='-'+str(self.last)
        return text

def number_tokens(tokens):
    out=[];i=0
    while i<len(tokens):
        t=tokens[i]
        if t not in UNITS and t not in TENS:
            out.append(t);i+=1;continue
        n=UNITS.get(t,TENS.get(t));i+=1
        if i<len(tokens) and tokens[i]=='hundred' and 1<=n<=9:
            n*=100;i+=1
            if i<len(tokens) and tokens[i]=='and':i+=1
            if i<len(tokens) and tokens[i] in TENS:
                n+=TENS[tokens[i]];i+=1
                if i<len(tokens) and tokens[i] in UNITS and UNITS[tokens[i]]<10:n+=UNITS[tokens[i]];i+=1
            elif i<len(tokens) and tokens[i] in UNITS:n+=UNITS[tokens[i]];i+=1
        elif t in TENS and i<len(tokens) and tokens[i] in UNITS and UNITS[tokens[i]]<10:
            n+=UNITS[tokens[i]];i+=1
        out.append(str(n))
    return out

def spoken_reference(transcript):
    text=unicodedata.normalize('NFKC',transcript).casefold().replace('–','-')
    text=re.sub(r'[^\w\s:\-]',' ',text)
    text=re.sub(r'\b(first|second|third)\s+(samuel|kings|chronicles|corinthians|thessalonians|timothy|peter|john)\b',lambda m:ORDINAL[m[1]]+' '+m[2],text)
    text=' '.join(number_tokens(re.findall(r'\d+|[a-z]+|[:\-]',text)))
    candidates=[]
    # Match canonical names and familiar full spoken variants, longest first.
    aliases={b.casefold():b for b in BOOKS}
    aliases.update({'psalm':'Psalms','song of songs':'Song of Solomon','revelations':'Revelation'})
    expression='|'.join(re.escape(a) for a in sorted(aliases,key=len,reverse=True))
    matches=list(re.finditer(r'(?<!\w)('+expression+r')(?!\w)',text))
    if len(matches)!=1:return None
    m=matches[0];book=aliases[m[1]];tail=text[m.end():].strip()
    tail=re.sub(r'\s+(please|thank you)$','',tail).strip()
    tail=re.sub(r'^chapter\s+','',tail)
    tail=re.sub(r'\bverses?\b',':',tail)
    tail=re.sub(r'\b(to|through|until)\b','-',tail)
    tail=re.sub(r'\s*([:\-])\s*',r'\1',tail)
    # Accept "John three sixteen", "John chapter three verse sixteen" and ranges.
    pattern=re.fullmatch(r'(\d+)(?:(?::|\s+)(\d+)(?:-(\d+))?)?',tail)
    if not pattern:return None
    chapter=int(pattern[1]);first=int(pattern[2]) if pattern[2] else None
    last=int(pattern[3]) if pattern[3] else first
    if chapter<1 or chapter>150 or (first is not None and (first<1 or first>176 or last<first or last>176)):return None
    return SpokenReference(book,chapter,first,last)

def validated_spoken_rows(rows, reference):
    found=search_rows(rows,reference.query)
    if not found:return []
    if reference.first is not None:
        actual=[int(r['verse']) for r in found]
        if actual!=list(range(reference.first,reference.last+1)):return []
    return found

def remembered_query(text):
    return re.sub(r'^\s*(?:find|search|look for)(?:\s+(?:the|a))?(?:\s+(?:bible|verse|passage|words))?(?:\s+(?:with|that says|saying))?\s+','',text,flags=re.I).strip()
