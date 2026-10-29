const $ = id => document.getElementById(id);
const canvas = $('video'), context = canvas.getContext('2d', { alpha: false });
const stage = $('stage');
let socket, decoder, configuration, needsKey = true, original = false;
let width = 720, height = 1560, lastCaptions = [], rendered = 0;
let translationStatus = 'Подключаю переводчик';
let pendingFrame=null,paintScheduled=false,hasPicture=false;
let scrollOffset=0,captionLabels=[],motionScheduled=false;
const pointers = new Map();
const pendingMoves=new Map();let moveScheduled=false;
$('status').textContent = 'Подключаю видеоканал…';
const send = message => { if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); };
function codecFor(bytes) {
  for (let i = 0; i < bytes.length - 6; i++) {
    let offset = -1;
    if (bytes[i] === 0 && bytes[i+1] === 0 && bytes[i+2] === 1) offset = i+3;
    else if (bytes[i] === 0 && bytes[i+1] === 0 && bytes[i+2] === 0 && bytes[i+3] === 1) offset = i+4;
    if (offset >= 0 && (bytes[offset] & 31) === 7)
      return 'avc1.' + [...bytes.slice(offset+1, offset+4)].map(x => x.toString(16).padStart(2,'0')).join('');
  }
  return 'avc1.42E01F';
}
function configure(bytes) {
  configuration = bytes;
  if (decoder && decoder.state !== 'closed') decoder.close();
  canvas.dataset.codec = codecFor(bytes);
  decoder = new VideoDecoder({
    output(frame) {
      if (canvas.width !== frame.displayWidth || canvas.height !== frame.displayHeight) {
        canvas.width = frame.displayWidth; canvas.height = frame.displayHeight;
        width = frame.displayWidth; height = frame.displayHeight;
        stage.style.aspectRatio = `${width}/${height}`;
      }
      pendingFrame?.close();pendingFrame=frame;
      if(!paintScheduled){paintScheduled=true;requestAnimationFrame(()=>{
        paintScheduled=false;
        if(!pendingFrame)return;
        context.drawImage(pendingFrame,0,0);pendingFrame.close();pendingFrame=null;
        rendered++;canvas.dataset.renderedFrames=String(rendered);applyCaptionMotion();
        if(!hasPicture){hasPicture=true;$('waiting').style.display='none';$('status').textContent=translationStatus;}
      });}
    },
    error(error) { needsKey = true; canvas.dataset.viewerError = error.message; $('status').textContent = 'Браузер не смог показать видео'; }
  });
  decoder.configure({ codec: codecFor(bytes), optimizeForLatency: true, hardwareAcceleration: 'prefer-hardware' });
  needsKey = true;
}
function packet(buffer) {
  canvas.dataset.receivedPackets = String(Number(canvas.dataset.receivedPackets || 0) + 1);
  const view = new DataView(buffer), flags = view.getBigUint64(0), bytes = new Uint8Array(buffer,12);
  if (flags & (1n<<62n)) { canvas.dataset.configPackets = String(Number(canvas.dataset.configPackets || 0) + 1); configure(new Uint8Array(bytes)); return; }
  const key = Boolean(flags & (1n<<61n));
  if (key) canvas.dataset.keyFrames = String(Number(canvas.dataset.keyFrames || 0) + 1);
  canvas.dataset.decoderState = decoder?.state || 'none';
  canvas.dataset.decodeQueue = String(decoder?.decodeQueueSize ?? -1);
  if (!decoder || decoder.state !== 'configured' || (needsKey && !key)) return;
  if (decoder.decodeQueueSize > 12) { configure(configuration); return; }
  let payload = bytes;
  if (key && configuration) { payload = new Uint8Array(configuration.length + bytes.length); payload.set(configuration); payload.set(bytes,configuration.length); }
  try {
    decoder.decode(new EncodedVideoChunk({ type:key?'key':'delta', timestamp:Number(flags & ((1n<<61n)-1n)), data:payload }));
    canvas.dataset.submittedFrames = String(Number(canvas.dataset.submittedFrames || 0) + 1);
    needsKey = false;
  } catch(error) { needsKey = true; canvas.dataset.viewerError=error.message; }
}
function captions(items) {
  lastCaptions=items;captionLabels=[];$('captions').replaceChildren();
  if(original)return;
  const scale=stage.clientWidth/width;
  const fragment=document.createDocumentFragment();
  for(const item of items){
    if(!['title','button'].includes(item.role))continue;
    const [x,y,w,h]=item.box;
    const label=document.createElement('span');label.className='caption';label.textContent=item.text;
    const size=Math.min(Number($('fontSize').value),h*height*.76)*scale;
    const lines=Math.max(1,Math.min(item.lines||1,Math.floor(h*height*scale/(size*1.12))));
    Object.assign(label.style,{left:`${x*100}%`,top:`${y*100}%`,width:`${w*100}%`,height:`${h*100}%`,fontSize:`${size}px`});
    label.style.setProperty('--caption-lines',String(lines));
    label.dataset.role=item.role;
    if($('autoColors').checked){
      const bg=/^#[0-9a-f]{6}$/i.test(item.background||'')?item.background:'#ffffff';
      const rgb=[1,3,5].map(i=>parseInt(bg.slice(i,i+2),16));
      label.style.background=bg;
      label.style.color=rgb[0]*.299+rgb[1]*.587+rgb[2]*.114>135?'#17272c':'#ffffff';
    }
    fragment.append(label);captionLabels.push({label,item});
  }
  $('captions').append(fragment);applyCaptionMotion();
}
function applyCaptionMotion(){
  const scale=stage.clientWidth/width;
  for(const {label,item} of captionLabels){
    if(!item.scroll_box)continue;
    const dy=scrollOffset-(item.scroll_ref||0);
    label.style.transform=`translate3d(0,${dy*scale}px,0)`;
    const top=item.box[1]*height+dy,bottom=(item.box[1]+item.box[3])*height+dy;
    const cutTop=Math.max(0,item.scroll_box[1]*height-top)*scale;
    const cutBottom=Math.max(0,bottom-item.scroll_box[3]*height)*scale;
    label.style.visibility=cutTop+cutBottom>=item.box[3]*height*scale?'hidden':'visible';
    label.style.clipPath=`inset(${cutTop}px 0 ${cutBottom}px 0)`;
  }
}
function setMotion(offset){
  scrollOffset=offset;
  if(!motionScheduled){motionScheduled=true;requestAnimationFrame(()=>{motionScheduled=false;applyCaptionMotion();});}
}

function connect() {
  socket = new WebSocket(`${location.protocol === 'https:'?'wss':'ws'}://${location.host}/stream`); socket.binaryType = 'arraybuffer';
  socket.onopen = () => { $('status').textContent = 'Видеоканал открыт, ожидаю изображение…'; };
  socket.onerror = () => { $('status').textContent = 'Не удалось открыть видеоканал'; };
  socket.onmessage = event => {
    if (typeof event.data !== 'string') {
      try { packet(event.data); } catch(error) { canvas.dataset.viewerError = error.message; $('status').textContent = 'Не удалось показать видеокадр'; }
      return;
    }
    const state = JSON.parse(event.data);
    if (state.width) { width=state.width; height=state.height; stage.style.aspectRatio=`${width}/${height}`; }
    if(state.scroll_offset!==undefined)setMotion(state.scroll_offset);
    if(state.motion)setMotion(state.motion.offset);
    if (state.translation_enabled !== undefined) {
      const changed=original===state.translation_enabled;
      original=!state.translation_enabled;$('original').textContent=original?'Перевод':'Оригинал';
      if(changed&&!state.captions)captions(lastCaptions);
    }
    if (state.captions) captions(state.captions);
    if (state.translation_status) { translationStatus = state.translation_status; $('status').textContent = translationStatus; }
    if (state.error) $('status').textContent=state.error;
    if (state.control_ready === false) { $('status').textContent=state.error || 'Управление переподключается…'; }
  };
  socket.onclose = () => { pointers.clear(); $('status').textContent='Переподключение…'; setTimeout(connect,1000); };
}
let stageRect;
function coordinates(event) {
  const rect = stageRect || (stageRect=stage.getBoundingClientRect());
  return {x:Math.min(width-1,Math.max(0,Math.round((event.clientX-rect.left)/rect.width*width))),y:Math.min(height-1,Math.max(0,Math.round((event.clientY-rect.top)/rect.height*height)))};
}
stage.addEventListener('pointerdown',event => {
  event.preventDefault(); if(pointers.size>=10) return;
  stage.focus({preventScroll:true});stageRect=stage.getBoundingClientRect();
  let id=0;while([...pointers.values()].includes(id))id++;
  pointers.set(event.pointerId,id);stage.setPointerCapture(event.pointerId);
  send({type:'touch',action:0,pointer:id,...coordinates(event)});
});
function flushMoves(){
  moveScheduled=false;
  for(const [pointer,point] of pendingMoves)send({type:'touch',action:2,pointer,...coordinates(point)});
  pendingMoves.clear();
}
stage.addEventListener('pointermove',event=>{
  if(!pointers.has(event.pointerId))return;event.preventDefault();
  pendingMoves.set(pointers.get(event.pointerId),{clientX:event.clientX,clientY:event.clientY});
  if(!moveScheduled){moveScheduled=true;requestAnimationFrame(flushMoves);}
});
for(const type of ['pointerup','pointercancel'])stage.addEventListener(type,event=>{
  if(!pointers.has(event.pointerId))return;
  flushMoves();
  send({type:'touch',action:type==='pointerup'?1:3,pointer:pointers.get(event.pointerId),...coordinates(event)});
  pointers.delete(event.pointerId);
});
function cancelPointers(){
  pendingMoves.clear();
  for(const id of pointers.values())send({type:'touch',action:3,pointer:id,x:0,y:0});
  pointers.clear();
}
window.addEventListener('blur',cancelPointers);
document.addEventListener('visibilitychange',()=>{if(document.hidden)cancelPointers();});
stage.addEventListener('lostpointercapture',event=>{
  const id=pointers.get(event.pointerId);if(id===undefined)return;
  pendingMoves.delete(id);send({type:'touch',action:3,pointer:id,x:0,y:0});pointers.delete(event.pointerId);
});
stage.addEventListener('keydown',event=>{
  if(event.ctrlKey||event.metaKey||event.altKey)return;
  const code={Enter:66,Backspace:67,Delete:112,Escape:4,Tab:61,ArrowUp:19,ArrowDown:20,ArrowLeft:21,ArrowRight:22}[event.key];
  if(code){event.preventDefault();send({type:'key',code});}
  else if(event.key.length===1){event.preventDefault();send({type:'text',text:event.key});}
});
stage.addEventListener('paste',event=>{
  const text=event.clipboardData?.getData('text/plain');
  if(text){event.preventDefault();send({type:'text',text:text.slice(0,1000)});}
});
$('back').onclick=()=>send({type:'key',code:4});$('home').onclick=()=>send({type:'key',code:3});
$('launch').onclick=()=>fetch('/open-pinduoduo',{method:'POST'});
$('original').onclick=()=>{original=!original;$('original').textContent=original?'Перевод':'Оригинал';send({type:'translation',enabled:!original});captions(lastCaptions);};
$('text').addEventListener('input',event=>{if(event.inputType==='deleteContentBackward')send({type:'key',code:67});else if(event.data)send({type:'text',text:event.data});$('text').value='';});
$('text').addEventListener('keydown',event=>{if(event.key==='Enter'){send({type:'key',code:66});$('text').blur();}});
$('settings').onclick=()=>$('appearance').showModal();$('closeSettings').onclick=()=>$('appearance').close();
for(const id of ['fontSize','textColor','bgColor','opacity','autoColors'])$(id).oninput=()=>{
  if(id==='textColor'||id==='bgColor'||id==='opacity')$('autoColors').checked=false;
  document.documentElement.style.setProperty('--text',$('textColor').value);document.documentElement.style.setProperty('--bg',$('bgColor').value);document.documentElement.style.setProperty('--opacity',Number($('opacity').value)/100);captions(lastCaptions);
};
if(!('VideoDecoder' in window))$('status').textContent='Браузеру нужна поддержка WebCodecs';else connect();
new ResizeObserver(()=>{stageRect=stage.getBoundingClientRect();captions(lastCaptions);}).observe(stage);
window.addEventListener('resize',()=>{stageRect=null;});
window.pinduoDiagnostics=()=>({renderedFrames:rendered,width,height,decoderState:decoder?.state,captions:lastCaptions.length});

let wheelDelta=0,wheelPoint=null,wheelTimer=null;
stage.addEventListener('wheel',event=>{
  event.preventDefault();wheelDelta+=Math.max(-240,Math.min(240,event.deltaY));wheelPoint=coordinates(event);
  if(!wheelTimer)wheelTimer=setTimeout(()=>{send({type:'scroll',amount:-Math.max(-4,Math.min(4,wheelDelta/120)),...wheelPoint});wheelDelta=0;wheelTimer=null;},35);
},{passive:false});
stage.addEventListener('pointermove',event=>{
  if(pointers.size){stage.title='';return;}
  const point=coordinates(event),nx=point.x/width,ny=point.y/height;
  const item=lastCaptions.find(item=>{
    const dy=item.scroll_box?(scrollOffset-(item.scroll_ref||0))/height:0;
    return nx>=item.box[0]&&nx<=item.box[0]+item.box[2]&&ny>=item.box[1]+dy&&ny<=item.box[1]+item.box[3]+dy;
  });
  stage.title=item?.text||'';
});

let statsFrames=0,statsAt=performance.now();
setInterval(()=>{
  const now=performance.now(),fps=(rendered-statsFrames)*1000/(now-statsAt);
  statsAt=now;statsFrames=rendered;
  if(document.hidden)return;
  if($('fps'))$('fps').textContent=fps>1?`${Math.round(fps)} fps`:'— fps';
  send({type:'stats',fps:Math.round(fps*10)/10,queue:decoder?.decodeQueueSize||0});
},1000);

async function refreshPhone(){
  const state=await(await fetch('/phone')).json();
  $('phoneStatus').textContent=state.connected?'iPhone подключён':state.status;
  $('pairQR').hidden=!state.active;
  if(state.active&&!$('pairQR').getAttribute('src'))$('pairQR').src='/phone/qr.png';
}
$('phoneButton').onclick=()=>{$('phoneDialog').showModal();refreshPhone().catch(()=>{});};
$('phoneClose').onclick=()=>$('phoneDialog').close();
$('phoneStart').onclick=async()=>{
  $('phoneStatus').textContent='Включаю защищённое подключение…';
  const response=await fetch('/phone/start',{method:'POST'});
  if(!response.ok){const error=await response.json();$('phoneStatus').textContent=error.detail||'Не удалось включить подключение';return;}
  await refreshPhone();
};
$('phoneStop').onclick=async()=>{await fetch('/phone/stop',{method:'POST'});$('pairQR').removeAttribute('src');await refreshPhone();};
setInterval(()=>{if($('phoneDialog').open)refreshPhone().catch(()=>{});},2000);
