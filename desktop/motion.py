"""Track scrolling from video pixels; no OCR or per-frame layout traversal."""
import cv2
import numpy as np


class ScrollMotion:
    def __init__(self):
        cv2.setNumThreads(1)
        self.previous=None
        self.points=None
        self.region=None
        self.frames=0

    def reset(self):
        self.previous=self.points=None

    def update(self,image):
        height,width=image.shape[:2]
        scale=min(1.,240/width)
        gray=cv2.cvtColor(cv2.resize(image,(round(width*scale),round(height*scale))),cv2.COLOR_RGB2GRAY)
        result=(0.,0.)
        if self.previous is not None and self.previous.shape==gray.shape:
            if self.points is None or len(self.points)<15 or self.frames%12==0:
                mask=np.zeros_like(gray)
                box=self.region or (0,height*.18,width,height*.91)
                x,y,right,bottom=[round(v*scale) for v in box]
                mask[max(0,y):min(gray.shape[0],bottom),max(0,x):min(gray.shape[1],right)]=255
                self.points=cv2.goodFeaturesToTrack(self.previous,100,.025,7,mask=mask)
            if self.points is not None:
                following,status,error=cv2.calcOpticalFlowPyrLK(self.previous,gray,self.points,None,
                    winSize=(21,21),maxLevel=3,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,15,.03))
                back,back_status,_=cv2.calcOpticalFlowPyrLK(gray,self.previous,following,None,
                    winSize=(21,21),maxLevel=3,criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,10,.05))
                p=self.points.reshape(-1,2);q=following.reshape(-1,2)
                valid=(status.ravel()>0)&(back_status.ravel()>0)&(np.linalg.norm(back.reshape(-1,2)-p,axis=1)<1.1)
                delta=q-p
                valid &= (np.abs(delta[:,0])<2.5)&(np.abs(delta[:,1])<gray.shape[0]*.35)
                if valid.sum()>=8:
                    values=delta[valid,1]
                    dy=float(np.median(values))
                    agreement=float((np.abs(values-dy)<1.25).mean())
                    if agreement>.60 and abs(dy)>.15:result=(dy/scale,agreement)
                self.points=following[valid].reshape(-1,1,2) if valid.sum()>8 else None
        self.previous=gray
        self.frames+=1
        return result
