"""Render presentations away from the operator event loop."""
from pathlib import Path
import uuid
import fitz
from PySide6.QtCore import QThread,Signal

class MediaImportWorker(QThread):
    progress=Signal(int,int,str)
    completed=Signal(list,list)
    def __init__(self,paths,output,convert,method,parent=None):
        super().__init__(parent);self.paths=paths;self.output=Path(output);self.convert=convert;self.method=method
    def run(self):
        items,errors=[],[]
        self.output.mkdir(parents=True,exist_ok=True)
        for name in self.paths:
            if self.isInterruptionRequested():break
            path=Path(name);self.progress.emit(0,0,'Opening '+path.name)
            generated=[]
            try:
                if not path.is_file():raise ValueError('File not found')
                suffix=path.suffix.lower()
                if suffix in ('.ppt','.pptx','.pdf'):
                    source=self.convert(path,self.method) if suffix!='.pdf' else path
                    with fitz.open(source) as doc:
                        total=len(doc)
                        if not total:raise ValueError('The presentation has no pages')
                        for index,page in enumerate(doc):
                            if self.isInterruptionRequested():break
                            # Cap rendered pages to Full HD to bound memory and decode cost.
                            scale=min(2.0,1920/max(page.rect.width,page.rect.height))
                            pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                            target=self.output/(uuid.uuid4().hex+'.png');pix.save(str(target));generated.append(target)
                            items.append({'kind':'image','path':str(target),'title':f'{path.name} • slide {index+1}',
                                          'presentation':path.name,'slide_number':index+1,'slide_count':total})
                            self.progress.emit(index+1,total,path.name)
                elif suffix in ('.mp4','.mov','.mkv','.webm','.avi'):
                    items.append({'kind':'video','path':str(path.resolve()),'title':path.name})
                elif suffix in ('.png','.jpg','.jpeg','.webp','.bmp'):
                    items.append({'kind':'image','path':str(path.resolve()),'title':path.name})
                else:raise ValueError('Unsupported file format')
            except Exception as error:
                # A failed deck must not leave a partly imported agenda.
                created={str(p) for p in generated}
                items=[item for item in items if item.get('path') not in created]
                for target in generated:target.unlink(missing_ok=True)
                errors.append(path.name+': '+str(error))
        if self.isInterruptionRequested():
            generated_items=[i for i in items if i.get('presentation')]
            for item in generated_items:Path(item['path']).unlink(missing_ok=True)
            self.completed.emit([],['Import cancelled.'])
        else:self.completed.emit(items,errors)
