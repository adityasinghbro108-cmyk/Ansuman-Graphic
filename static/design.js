(() => {
  const studio = document.getElementById('studio');
  if (!studio) return;
  const $ = id => document.getElementById(id);
  const modal = $('designModal'), drawer = $('cartDrawer');
  const area = $('printArea'), mockup = $('mockup'), form = $('designForm');
  const previewStage = $('previewStage'), threeHost = $('threePreview');
  const photoSource = $('productPhoto'), photoCanvas = $('photoComposition');
  const input = $('imageInput'), multipleInput = $('multipleInput'), submitInput = $('submitImages');
  const readyInput = $('readyImageInput');
  const productSelect = $('productSelect');
  const locationSelect = $('printLocationSelect');
  const catalog = JSON.parse(studio.dataset.catalog);
  const maxPhotos = Number(studio.dataset.maxArtworks || 24), maxBytes = 12 * 1024 * 1024;
  const maxTotalBytes = Number(studio.dataset.createTotalMb || 96) * 1024 * 1024;
  const maxReadyBytes = 80 * 1024 * 1024, maxReadyPixels = 120_000_000;
  const art = [];
  const productStates = new Map();
  const undoStack = [], redoStack = [];
  let currentSlug = studio.dataset.product, currentProduct = catalog[currentSlug], designMode = 'create';
  let previewMode = 'three';
  let currentView = 'front', photoList = [], activePhoto = null, photoDrag = null;
  let defaultLocation = currentProduct?.print_areas?.[0]?.id || 'front';
  let activeId = null, productAngle = 0, productZoom = 1, currentColor = 'white', backView = false, turning = false, turnStart = null, productPreview = null;
  const selected = () => art.find(item => item.id === activeId);
  const artworkUrl = item => item.uploadedUrl || item.url;
  const productAsset = product => ({model:product.model_file, license:product.model_license, print_surfaces:product.print_surfaces || {}});

  const showError = message => { const node = $('uploadError'); node.textContent = message; node.hidden = false; };
  const clearError = () => { $('uploadError').hidden = true; $('uploadError').textContent = ''; };
  const clearArt = () => { art.splice(0).forEach(item => URL.revokeObjectURL(item.url)); activeId = null; renderGallery(); renderPreview(); syncSettings(); };
  const setDesignMode = mode => {
    if (!['create', 'ready'].includes(mode)) return;
    if (mode !== designMode && art.length) clearArt();
    designMode = mode; $('designModeField').value = mode;
    const ready = mode === 'ready';
    $('createDesignControls').hidden = ready; $('readyDesignControls').hidden = !ready;
    $('createModeButton').classList.toggle('active', !ready); $('createModeButton').setAttribute('aria-pressed', String(!ready));
    $('readyModeButton').classList.toggle('active', ready); $('readyModeButton').setAttribute('aria-pressed', String(ready));
    $('modeDescription').textContent = ready ? 'Upload one finished image. It will be previewed without editing or cropping.' : 'Add photos and arrange them on your product.';
    studio.classList.toggle('ready-design-mode', ready);
  };
  const previewWarning = message => { const node = $('previewWarning'); if (node) { node.textContent = message; node.hidden = !message; } };
  document.querySelectorAll('img[data-fallback]').forEach(image => image.addEventListener('error', () => {
    if (image.dataset.fallbackApplied) return;
    image.dataset.fallbackApplied = 'true'; image.src = image.dataset.fallback;
  }));
  const areaLogicalSize = location => {
    const rect=area.getBoundingClientRect(); if(rect.width>1&&rect.height>1)return {width:rect.width,height:rect.height};
    const ratio=({left_chest:1,right_chest:1,left_sleeve:.65,right_sleeve:.65,front_center:.79,back_center:.79})[location]||.8;
    return {width:600,height:600/ratio};
  };
  const defaultPhotoBoundaries = {
    front:{front_center:[[.37,.33],[.63,.33],[.63,.57],[.37,.57]],left_chest:[[.44,.34],[.56,.34],[.56,.46],[.44,.46]],right_chest:[[.44,.34],[.56,.34],[.56,.46],[.44,.46]]},
    back:{back_center:[[.36,.31],[.64,.31],[.64,.63],[.36,.63]]},
    left:{left_sleeve:[[.42,.35],[.58,.35],[.58,.56],[.42,.56]]},
    right:{right_sleeve:[[.42,.35],[.58,.35],[.58,.56],[.42,.56]]},
  };
  const photoBoundary = (view,location) => currentProduct?.photo_print_areas?.[view]?.[location] || defaultPhotoBoundaries[view]?.[location] || null;
  const mapQuad = (corners,u,v) => {
    const [p0,p1,p2,p3]=corners.map(([x,y])=>({x:x*photoCanvas.width,y:y*photoCanvas.height}));
    const dx1=p1.x-p2.x,dx2=p3.x-p2.x,dx3=p0.x-p1.x+p2.x-p3.x;
    const dy1=p1.y-p2.y,dy2=p3.y-p2.y,dy3=p0.y-p1.y+p2.y-p3.y;
    const denominator=dx1*dy2-dx2*dy1; let g=0,h=0;
    if(Math.abs(dx3)+Math.abs(dy3)>1e-8&&Math.abs(denominator)>1e-8){g=(dx3*dy2-dx2*dy3)/denominator;h=(dx1*dy3-dx3*dy1)/denominator;}
    const a=p1.x-p0.x+g*p1.x,b=p1.y-p0.y+g*p1.y,c=p3.x-p0.x+h*p3.x,d=p3.y-p0.y+h*p3.y;
    const divisor=g*u+h*v+1; return {x:(a*u+c*v+p0.x)/divisor,y:(b*u+d*v+p0.y)/divisor};
  };
  const drawMappedTriangle = (ctx,image,s0,s1,s2,d0,d1,d2) => {
    const sx1=s1.x-s0.x,sy1=s1.y-s0.y,sx2=s2.x-s0.x,sy2=s2.y-s0.y,det=sx1*sy2-sx2*sy1;
    if(Math.abs(det)<1e-8)return;
    const dx1=d1.x-d0.x,dy1=d1.y-d0.y,dx2=d2.x-d0.x,dy2=d2.y-d0.y;
    const a=(dx1*sy2-dx2*sy1)/det,c=(dx2*sx1-dx1*sx2)/det,b=(dy1*sy2-dy2*sy1)/det,d=(dy2*sx1-dy1*sx2)/det,e=d0.x-a*s0.x-c*s0.y,f=d0.y-b*s0.x-d*s0.y;
    const sx=Math.min(s0.x,s1.x,s2.x),sy=Math.min(s0.y,s1.y,s2.y),sw=Math.max(s0.x,s1.x,s2.x)-sx,sh=Math.max(s0.y,s1.y,s2.y)-sy;
    ctx.save();ctx.beginPath();ctx.moveTo(d0.x,d0.y);ctx.lineTo(d1.x,d1.y);ctx.lineTo(d2.x,d2.y);ctx.closePath();ctx.clip();ctx.transform(a,b,c,d,e,f);ctx.drawImage(image,sx,sy,sw,sh,sx,sy,sw,sh);ctx.restore();
  };
  const warpPrintArea = (ctx,texture,corners) => {
    const steps=16,tw=texture.width,th=texture.height;
    for(let y=0;y<steps;y++)for(let x=0;x<steps;x++){
      const u0=x/steps,u1=(x+1)/steps,v0=y/steps,v1=(y+1)/steps;
      const s00={x:u0*tw,y:v0*th},s10={x:u1*tw,y:v0*th},s11={x:u1*tw,y:v1*th},s01={x:u0*tw,y:v1*th};
      drawMappedTriangle(ctx,texture,s00,s10,s11,mapQuad(corners,u0,v0),mapQuad(corners,u1,v0),mapQuad(corners,u1,v1));
      drawMappedTriangle(ctx,texture,s00,s11,s01,mapQuad(corners,u0,v0),mapQuad(corners,u1,v1),mapQuad(corners,u0,v1));
    }
  };
  const renderPhotoComposition = () => {
    if(!photoCanvas)return;
    const ctx=photoCanvas.getContext('2d');ctx.clearRect(0,0,photoCanvas.width,photoCanvas.height);
    if(!activePhoto||!photoSource.complete||!photoSource.naturalWidth){photoCanvas.hidden=true;return;}
    if(photoCanvas.width!==photoSource.naturalWidth||photoCanvas.height!==photoSource.naturalHeight){photoCanvas.width=photoSource.naturalWidth;photoCanvas.height=photoSource.naturalHeight;}
    photoCanvas.hidden=false;ctx.clearRect(0,0,photoCanvas.width,photoCanvas.height);ctx.drawImage(photoSource,0,0);
    const view=activePhoto.view,viewLayers=art.filter(item=>(item.view||'front')===view),locations=[...new Set(viewLayers.map(item=>item.location))],missing=[];
    for(const location of locations){
      const corners=photoBoundary(view,location);if(!corners){missing.push(location);continue;}
      const logical=areaLogicalSize(location),texture=document.createElement('canvas');texture.width=1000;texture.height=Math.max(1,Math.round(1000*logical.height/logical.width));
      const tctx=texture.getContext('2d');
      for(const item of viewLayers.filter(layer=>layer.location===location)){
        let image=item.previewImage;
        if(!image){image=new Image();item.previewImage=image;image.onload=renderPhotoComposition;image.src=artworkUrl(item);}
        if(!image.complete||!image.naturalWidth)continue;
        const fit=Math.min(texture.width*.74/image.naturalWidth,texture.height*.74/image.naturalHeight),w=image.naturalWidth*fit,h=image.naturalHeight*fit;
        const cx=texture.width/2+(item.x/logical.width)*texture.width,cy=texture.height/2+(item.y/logical.height)*texture.height;
        tctx.save();tctx.translate(cx,cy);tctx.rotate((item.angle||0)*Math.PI/180);tctx.drawImage(image,-w*item.scale/2,-h*item.scale/2,w*item.scale,h*item.scale);tctx.restore();
      }
      warpPrintArea(ctx,texture,corners);
      if(selected()?.location===location&&selected()?.view===view){ctx.save();ctx.beginPath();corners.forEach(([x,y],index)=>index?ctx.lineTo(x*photoCanvas.width,y*photoCanvas.height):ctx.moveTo(x*photoCanvas.width,y*photoCanvas.height));ctx.closePath();ctx.strokeStyle='#24d5e8';ctx.lineWidth=Math.max(2,photoCanvas.width/900);ctx.setLineDash([10,7]);ctx.stroke();ctx.restore();}
    }
    const configured=locations.some(location=>currentProduct?.photo_print_areas?.[view]?.[location]);
    const status=$('photoCompositionStatus');
    if(status){status.hidden=!viewLayers.length&&!missing.length;status.textContent=missing.length?`No photo print boundary is configured for: ${missing.join(', ')}. Add normalized corner maps in Admin → Products.`:viewLayers.length?(configured?'Approximate photo composition · using administrator-configured perspective boundaries. Confirm placement against the actual garment before production.':'Approximate photo mockup composition · default estimated boundaries are not verified physical print placement.'):'No design layers are assigned to this photo view.';}
  };
  const updateThreeArtwork = () => {
    const sceneArt=art.map(item=>{const logical=areaLogicalSize(item.location);return {...item,previewX:item.x/logical.width,previewY:item.y/logical.height};});
    productPreview?.updateArtwork(sceneArt, areaLogicalSize(defaultLocation));renderPhotoComposition();
  };
  const mountThreePreview = () => {
    if (productPreview || !window.AnsumanProductPreview) return;
    try {
      productPreview = new window.AnsumanProductPreview(threeHost, previewStage, currentSlug, currentColor, productAsset(currentProduct));
      threeHost.hidden = false; mockup.hidden = true;
      productPreview.updateArtwork(art, area.getBoundingClientRect());
      productPreview.setView(productAngle, productZoom, backView); productPreview.resize();
      $('stageHint').textContent = 'Drag the product to rotate · artwork controls stay attached';
    } catch (error) {
      productPreview = null; threeHost.hidden = true; mockup.hidden = false;
      previewWarning('3D preview is unavailable in this browser. The interactive product preview is still available below.');
      console.warn('Three.js preview could not start; using the built-in product preview.', error);
    }
  };
  window.addEventListener('ansuman-three-ready', mountThreePreview);
  window.addEventListener('ansuman-three-failed', event => {
    threeHost.hidden = true; mockup.hidden = false;
    const reason = event.detail?.message ? ` (${event.detail.message})` : '';
    previewWarning(`3D preview could not load${reason}. The interactive product preview remains available; reload the page to retry.`);
  });
  window.addEventListener('ansuman-preview-quality', event => {
    if (event.detail?.quality === 'licensed-model') { previewWarning(''); return; }
    previewWarning(`Stylized preview active. ${event.detail?.reason || 'A licensed photorealistic model is not installed for this product.'} See static/models/README.md for asset requirements.`);
  });
  const selectPhotoImage = photo => {
    activePhoto=photo||null;const message=$('photoViewerMessage'),sourceNote=$('photoSourceNote');photoSource.hidden=true;photoCanvas.hidden=true;
    if(activePhoto){
      message.hidden=true;photoSource.alt=activePhoto.source==='mockup'?`${currentProduct.name} generic front mockup reference`:`${currentProduct.name} ${activePhoto.view} product photo`;
      if(sourceNote){sourceNote.hidden=activePhoto.source!=='mockup';sourceNote.textContent=activePhoto.source==='mockup'?'Generic front mockup reference ? not a photograph of the manufactured product ? artwork is an approximate composition only':'';}
      photoCanvas.hidden=false;photoSource.onload=()=>{photoCanvas.width=photoSource.naturalWidth;photoCanvas.height=photoSource.naturalHeight;renderPhotoComposition();};photoSource.onerror=()=>{photoCanvas.hidden=true;message.hidden=false;message.textContent='This product photo could not be loaded. Choose another view or contact the shop administrator.';};photoSource.src=`/static/${activePhoto.path}`;
    }else{message.hidden=false;message.textContent=`No ${currentView} product photo is available. The design remains in the 3D preview; an administrator can upload this photo view.`;if(sourceNote){sourceNote.hidden=true;sourceNote.textContent='';}}
    document.querySelectorAll('.photo-thumb').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.photo===activePhoto?.path)));
  };
  const activateProductView = (view, photo = null, persist = true) => {
    currentView = ['front','back','left','right','detail','side'].includes(view) ? view : 'front';
    if(currentView==='front'){backView=false;productAngle=0;defaultLocation=currentProduct.print_areas?.find(entry=>entry.id==='front_center')?.id||currentProduct.print_areas?.[0]?.id||defaultLocation;}
    else if(currentView==='back'){backView=true;productAngle=0;defaultLocation=currentProduct.print_areas?.find(entry=>entry.id==='back_center')?.id||currentProduct.print_areas?.[0]?.id||defaultLocation;}
    else if(currentView==='left'){backView=false;productAngle=-90;defaultLocation=currentProduct.print_areas?.find(entry=>entry.id==='left_sleeve')?.id||currentProduct.print_areas?.[0]?.id||defaultLocation;}
    else if(currentView==='right'||currentView==='side'){backView=false;productAngle=90;defaultLocation=currentProduct.print_areas?.find(entry=>entry.id==='right_sleeve')?.id||currentProduct.print_areas?.[0]?.id||defaultLocation;}
    if(!selected()||selected().view!==currentView){const candidate=art.find(item=>item.view===currentView);activeId=candidate?.id||null;if(candidate)defaultLocation=candidate.location;}
    selectPhotoImage(photo||photoList.find(entry=>entry.view===currentView)||null);
    applyProductView();syncSettings();renderPreview();if(persist)saveProductState();
  };
  const renderProductPhotos = product => {
    const list=$('photoThumbnails'),message=$('photoViewerMessage');if(!list||!photoSource||!message)return;
    list.replaceChildren();
    photoList=Object.entries(product.photos||{}).flatMap(([view,paths])=>(Array.isArray(paths)?paths:[]).map((path,index)=>({view,path,index,source:'product'})));
    if(!photoList.some(photo=>photo.view==='front')&&product.product_image)photoList.unshift({view:'front',path:product.product_image,index:0,source:'product'});
    if(product.slug==='tshirt'&&!photoList.some(photo=>photo.view==='front'))photoList.push({view:'front',path:'mockups/sources/tshirt-front-pexels-mockupbee-12039633.jpg',index:0,source:'mockup'});
    photoList.forEach((photo,index)=>{
      const button=document.createElement('button');button.type='button';button.className='photo-thumb';button.dataset.photo=photo.path;
      button.setAttribute('aria-label',`${photo.view} ${photo.source==='mockup'?'mockup reference':'photo'} ${photo.index+1}`);button.setAttribute('aria-pressed','false');
      const thumb=document.createElement('img');thumb.src=`/static/${photo.path}`;thumb.alt='';button.append(thumb);
      const caption=document.createElement('small');caption.textContent=photo.view+(photo.source==='mockup'?' · mockup':'');button.append(caption);
      button.addEventListener('click',()=>activateProductView(photo.view,photo));list.append(button);
    });
    activePhoto=photoList.find(photo=>photo.view===currentView)||null;
    if(activePhoto)activateProductView(currentView,activePhoto,false);
    else {photoCanvas.hidden=true;message.hidden=false;message.textContent=`No ${currentView} product photo is available. The design remains in the 3D preview; an administrator can upload this photo view.`;}
  };
  const photoTransform = corners => {
    const p=corners.map(([x,y])=>({x:x*photoCanvas.width,y:y*photoCanvas.height})),[p0,p1,p2,p3]=p;
    const dx1=p1.x-p2.x,dx2=p3.x-p2.x,dx3=p0.x-p1.x+p2.x-p3.x,dy1=p1.y-p2.y,dy2=p3.y-p2.y,dy3=p0.y-p1.y+p2.y-p3.y,den=dx1*dy2-dx2*dy1;
    let g=0,h=0;if(Math.abs(dx3)+Math.abs(dy3)>1e-8&&Math.abs(den)>1e-8){g=(dx3*dy2-dx2*dy3)/den;h=(dx1*dy3-dx3*dy1)/den;}
    const m=[p1.x-p0.x+g*p1.x,p3.x-p0.x+h*p3.x,p0.x,p1.y-p0.y+g*p1.y,p3.y-p0.y+h*p3.y,p0.y,g,h,1];
    const [a,b,c,d,e,f,g2,h2,i]=m,det=a*(e*i-f*h2)-b*(d*i-f*g2)+c*(d*h2-e*g2);if(Math.abs(det)<1e-8)return null;
    return [(e*i-f*h2)/det,(c*h2-b*i)/det,(b*f-c*e)/det,(f*g2-d*i)/det,(a*i-c*g2)/det,(c*d-a*f)/det,(d*h2-e*g2)/det,(b*g2-a*h2)/det,(a*e-b*d)/det];
  };
  const projectPoint = (m,x,y) => {const w=m[6]*x+m[7]*y+m[8];return Math.abs(w)<1e-8?null:{x:(m[0]*x+m[1]*y+m[2])/w,y:(m[3]*x+m[4]*y+m[5])/w};};
  const photoPointerPoint = event => {
    const rect=photoCanvas.getBoundingClientRect();if(!rect.width||!rect.height||!photoCanvas.width||!photoCanvas.height)return null;
    const scale=Math.min(rect.width/photoCanvas.width,rect.height/photoCanvas.height),drawW=photoCanvas.width*scale,drawH=photoCanvas.height*scale;
    return {x:(event.clientX-rect.left-(rect.width-drawW)/2)/scale,y:(event.clientY-rect.top-(rect.height-drawH)/2)/scale};
  };
  photoCanvas.addEventListener('pointerdown',event=>{
    const item=selected();if(previewMode!=='photo'||designMode==='ready'||!activePhoto||!item||(item.view||'front')!==activePhoto.view)return;
    const corners=photoBoundary(activePhoto.view,item.location),point=photoPointerPoint(event),inverse=corners&&photoTransform(corners);if(!inverse||!point)return;
    const local=projectPoint(inverse,point.x,point.y);if(!local||local.x<0||local.x>1||local.y<0||local.y>1)return;
    const logical=areaLogicalSize(item.location);photoDrag={item,offsetX:logical.width/2+item.x-local.x*logical.width,offsetY:logical.height/2+item.y-local.y*logical.height,inverse,logical};
    photoCanvas.setPointerCapture(event.pointerId);event.preventDefault();
  });
  photoCanvas.addEventListener('pointermove',event=>{
    if(!photoDrag)return;const point=photoPointerPoint(event),local=point&&projectPoint(photoDrag.inverse,point.x,point.y);if(!local)return;
    const {item,logical,offsetX,offsetY}=photoDrag;item.x=Math.max(-logical.width*.35,Math.min(logical.width*.35,local.x*logical.width+offsetX-logical.width/2));item.y=Math.max(-logical.height*.35,Math.min(logical.height*.35,local.y*logical.height+offsetY-logical.height/2));
    applyArtTransform(item);syncSettings();saveProductState();renderPhotoComposition();
  });
  ['pointerup','pointercancel','lostpointercapture'].forEach(type=>photoCanvas.addEventListener(type,()=>{photoDrag=null;}));
  const setPreviewMode = mode => {
    previewMode = mode === 'photo' ? 'photo' : 'three'; const photo = previewMode === 'photo';
    previewStage.classList.toggle('photo-mode', photo);
    $('photoView').hidden = !photo; threeHost.hidden = photo || !productPreview; mockup.hidden = photo || !!productPreview;
    $('threeModeButton').classList.toggle('active', !photo); $('photoModeButton').classList.toggle('active', photo);
    $('threeModeButton').setAttribute('aria-pressed', String(!photo)); $('photoModeButton').setAttribute('aria-pressed', String(photo));
    if (!photo) productPreview?.resize();
    else {renderProductPhotos(currentProduct);renderPhotoComposition();}
  };
  $('threeModeButton').addEventListener('click', () => setPreviewMode('three'));
  $('photoModeButton').addEventListener('click', () => setPreviewMode('photo'));
  $('photoFullscreen').addEventListener('click', async () => { try { if (!document.fullscreenElement) await $('photoView').requestFullscreen(); else await document.exitFullscreen(); } catch { $('photoView').classList.toggle('photo-expanded'); } });
  const applyProductView = (animate = true) => {
    mockup.style.transition = animate ? 'transform .32s ease' : 'none';
    mockup.style.transform = `perspective(900px) rotateY(${productAngle + (backView ? 180 : 0)}deg) rotateX(${Math.max(-5, Math.min(5, productAngle / 18))}deg) scale(${productZoom})`;
    productPreview?.setView(productAngle, productZoom, backView);
    const normalizedAngle=((productAngle%360)+360)%360;
    const angleView=normalizedAngle<45||normalizedAngle>=315?'front':normalizedAngle<135?'right':normalizedAngle<225?'back':'left';
    if(currentView!=='detail')currentView=backView?'back':angleView;
    const activeView=currentView;
    $('tshirtViewControls').hidden=currentSlug!=='tshirt';
    [['tshirtFrontView','front'],['tshirtBackView','back'],['tshirtSideView','side'],['tshirtLeftView','left'],['tshirtRightView','right']].forEach(([id,view])=>$(id).setAttribute('aria-pressed',String(view===activeView)));
  };
  const setTshirtView = view => {
    if(currentSlug!=='tshirt'||!['front','back','left','right','side'].includes(view))return;
    activateProductView(view==='side'?'right':view);
  };
  const applyArtTransform = item => {
    const node = area.querySelector(`[data-art-id="${item.id}"]`);
    if (node) node.style.transform = `translate(calc(-50% + ${item.x}px), calc(-50% + ${item.y}px)) scale(${item.scale}) rotate(${item.angle}deg)`;
    updateThreeArtwork();
  };
  const syncSettings = () => {
    const transfer = new DataTransfer();
    art.forEach(item => transfer.items.add(item.file));
    submitInput.files = transfer.files;
    const angle = ((productAngle % 360) + 360) % 360;
    const view = currentView;
    const layers = art.map((item, layer_order) => ({
      layer_id: String(item.id), layer_order, type: item.textSpec ? 'text' : 'image',
      scale: item.scale, rotation: item.angle, offset_x: item.x, offset_y: item.y,
      position_x: item.x / Math.max(1, areaLogicalSize(item.location).width), position_y: item.y / Math.max(1, areaLogicalSize(item.location).height),
      width_ratio: .82 * item.scale, height_ratio: .82 * item.scale,
      location: item.location, view: item.view || currentView, text_spec: item.textSpec || null,
    }));
    $('artworkSettings').value = JSON.stringify(layers);
    $('designSnapshot').value = JSON.stringify({
      schema_version: 1, product: currentSlug, variant: $('sizeSelect').value,
      color: currentColor, mode: designMode, view, product_angle: productAngle,
      product_zoom: productZoom, default_location: defaultLocation,
      print_areas: [...new Set(layers.map(layer => layer.location))], layers,
    });
    $('artworkCount').textContent = `${art.length} / ${maxPhotos}`;
    $('fileName').textContent = art.length ? `${art.length} photo${art.length === 1 ? '' : 's'} ready` : 'PNG or JPG · up to 12 MB each';
    $('printHint').hidden = art.length > 0;
    $('removeBtn').disabled = !selected();
    $('selectedArtworkName').textContent = selected() ? selected().file.name : 'SELECT A PHOTO';
    if (selected()) {
      $('scaleField').value = selected().scale; $('rotationField').value = selected().angle;
      $('xField').value = selected().x; $('yField').value = selected().y;
      $('zoomRange').value = Math.round(selected().scale * 100); $('rotateRange').value = selected().angle;
      $('zoomValue').value = `${Math.round(selected().scale * 100)}%`; $('rotateValue').value = `${selected().angle}°`;
    }
    const spec=selected()?.textSpec, textControls=$('textFontSelect').closest('.text-style-controls');
    textControls.hidden=!spec;
    if(spec){$('textFontSelect').value=spec.font;$('textColorSelect').value=spec.color;$('textSizeInput').value=spec.size;$('textBoldInput').checked=spec.bold;$('textItalicInput').checked=spec.italic;}
  };
  const renderGallery = () => {
    const gallery = $('artworkGallery'); gallery.replaceChildren();
    if (!art.length) { const empty = document.createElement('p'); empty.className = 'gallery-empty'; empty.textContent = 'Your uploaded photos will appear here.'; gallery.append(empty); }
    art.forEach((item, index) => {
      const card = document.createElement('button'); card.type = 'button'; card.className = `artwork-thumb${item.id === activeId ? ' active' : ''}`;
      card.setAttribute('aria-label', `Select photo ${index + 1}: ${item.file.name}`); card.title = item.file.name;
      const image = document.createElement('img'); image.src = artworkUrl(item); image.alt = '';
      const number = document.createElement('span'); number.textContent = String(index + 1);
      card.append(image, number); card.addEventListener('click', () => { activeId = item.id; setPrintLocation(item.location, false); activateProductView(item.view||'front'); renderGallery(); }); gallery.append(card);
    });
  };
  const addPreviewNode = item => {
    const image = document.createElement('img'); image.className = 'preview-art'; image.dataset.artId = item.id;
    image.src = artworkUrl(item); image.alt = `Uploaded design ${art.indexOf(item) + 1}`; area.append(image); applyArtTransform(item);
    image.hidden = item.location !== (selected()?.location || defaultLocation) || (item.view||'front') !== currentView;
  };
  const renderPreview = () => {
    area.querySelectorAll('.preview-art').forEach(node => node.remove()); art.forEach(addPreviewNode);
    const location = selected()?.location || defaultLocation;
    area.dataset.location = location;
    locationSelect.value = location;
    const locationLabel = currentProduct.print_areas?.find(area => area.id === location)?.label || location.replaceAll('_', ' ');
    $('printAreaStatus').textContent = `Print area: ${locationLabel} · Artwork stays inside this supported area.`;
    $('bannerReference').hidden = currentSlug !== 'banner';
    previewStage.classList.toggle('banner-preview-stage', currentSlug === 'banner');
    if (productPreview) productPreview.setProduct(currentSlug, currentColor, productAsset(currentProduct));
    updateThreeArtwork();
  };
  const colorSwatches = {white:['Cloud white','#f1f3f8'],black:['Midnight','#171923'],navy:['Deep navy','#283653'],red:['Ruby red','#c93855'],blue:['Electric blue','#3477d1'],pink:['Rose','#ef9cb9'],yellow:['Sunshine','#f4c95d']};
  const renderColorSwatches = product => {
    const wrap=document.querySelector('.swatches');wrap.replaceChildren();
    (product.colors||[]).forEach(color=>{if(!colorSwatches[color])return;const button=document.createElement('button');button.type='button';button.className=`swatch${color===currentColor?' active':''}`;button.dataset.color=color;button.title=colorSwatches[color][0];button.style.setProperty('--swatch',colorSwatches[color][1]);button.addEventListener('click',()=>applyColor(color));wrap.append(button);});
  };
  const setPrintLocation = (location, updateSelected = true) => {
    if (!currentProduct.print_areas?.some(area => area.id === location)) return false;
    defaultLocation = location;
    if (updateSelected && selected()) selected().location = location;
    locationSelect.value = location;
    return true;
  };
  const addFiles = files => {
    clearError();
    const incoming = Array.from(files);
    if (!incoming.length) return;
    if (art.length + incoming.length > maxPhotos) { showError(`You can add up to ${maxPhotos} photos to one product.`); return; }
    const accepted = [];
    for (const file of incoming) {
      if (!['image/png', 'image/jpeg'].includes(file.type) || !/\.(png|jpe?g)$/i.test(file.name)) { showError(`${file.name}: use a PNG, JPG or JPEG image.`); continue; }
      if (file.size > maxBytes) { showError(`${file.name}: each image must be 12 MB or smaller.`); continue; }
      accepted.push(file);
    }
    let acceptedBytes = art.reduce((sum, item) => sum + item.file.size, 0);
    for (let index = accepted.length - 1; index >= 0; index--) {
      if (acceptedBytes + accepted[index].size > maxTotalBytes) { showError(`Combined artwork files must total ${Math.floor(maxTotalBytes / (1024 * 1024))} MB or less.`); acceptedBytes -= accepted[index].size; accepted.splice(index, 1); }
    }
    const addedItems = [];
    accepted.forEach(file => {
      const slot = art.length, positions = [[-.19,-.18],[.19,-.18],[-.19,.18],[.19,.18],[-.23,0],[.23,0],[0,-.27],[0,.27]];
      const position = positions[slot] || [0,0];
      const url = URL.createObjectURL(file), previewImage = new Image();
      const item = {id: window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`, file,
        url, previewImage, location: defaultLocation, view:currentView, scale: slot ? .58 : 1, angle: 0,
        x: areaLogicalSize(defaultLocation).width * position[0], y: areaLogicalSize(defaultLocation).height * position[1]};
      previewImage.onload = () => { if (art.includes(item)) updateThreeArtwork(); };
      previewImage.onerror = () => {
        const failedIndex = art.indexOf(item); if (failedIndex < 0) return;
        art.splice(failedIndex, 1); if (activeId === item.id) activeId = art.length ? art[Math.min(failedIndex, art.length - 1)].id : null;
        URL.revokeObjectURL(url); showError(`${file.name}: the browser could not decode this image. Try exporting it as PNG or JPG.`);
        renderGallery(); renderPreview(); syncSettings(); saveProductState();
      };
      art.push(item); addedItems.push(item); previewImage.src = url;
    });
    if (accepted.length && !activeId) activeId = art[0].id;
    if (addedItems.length) { renderGallery(); syncSettings(); addedItems.forEach(addPreviewNode); updateThreeArtwork(); saveProductState(); }
    input.value = ''; multipleInput.value = '';
  };
  const addReadyFile = file => {
    $('readyUploadError').hidden = true; $('readyWarning').hidden = true;
    if (!file) return;
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['png', 'jpg', 'jpeg', 'webp'].includes(ext) || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      $('readyUploadError').textContent = 'Choose a PNG, JPG, JPEG or WebP image.'; $('readyUploadError').hidden = false; readyInput.value = ''; return;
    }
    if (file.size > maxReadyBytes) {
      $('readyUploadError').textContent = 'This file is larger than the 80 MB limit.'; $('readyUploadError').hidden = false; readyInput.value = ''; return;
    }
    clearArt();
    const url = URL.createObjectURL(file), previewImage = new Image();
    const item = {id: window.crypto?.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random()}`, file, url, previewImage, location: defaultLocation, view:currentView, scale: 1, angle: 0, x: 0, y: 0};
    art.push(item); activeId = item.id;
    previewImage.onload = () => {
      if (previewImage.naturalWidth * previewImage.naturalHeight > maxReadyPixels) {
        art.splice(art.indexOf(item), 1); activeId = art.length ? art[0].id : null; URL.revokeObjectURL(url);
        $('readyUploadError').textContent = 'This image exceeds the 120 megapixel resolution limit.'; $('readyUploadError').hidden = false;
        $('readyUploadStatus').textContent = 'No valid file selected.'; readyInput.value = ''; renderPreview(); syncSettings(); return;
      }
      updateThreeArtwork();
      const ratios = {tshirt:.91,mug:1.7,pillow:1.16,banner:1.93,frame:.78,phonecase:.51,poster:.76,gift:1.06};
      const ratio = previewImage.naturalWidth / previewImage.naturalHeight, target = ratios[currentSlug] || 1;
      const warnings = [];
      if (Math.max(ratio / target, target / ratio) > 1.2) warnings.push('This image has a different shape from the printable area. The preview preserves the full image without cropping; confirm it suits your print.');
      if (Math.min(previewImage.naturalWidth, previewImage.naturalHeight) < 1200) warnings.push('This image may print soft at larger sizes. For best results, use artwork at least 1200 pixels on its shorter side.');
      $('readyWarning').textContent = warnings.join(' '); $('readyWarning').hidden = warnings.length === 0;
    };
    previewImage.onerror = () => { art.splice(art.indexOf(item), 1); activeId = art.length ? art[0].id : null; URL.revokeObjectURL(url); $('readyUploadError').textContent = 'This image could not be previewed. Try exporting it as PNG, JPG or WebP.'; $('readyUploadError').hidden = false; readyInput.value = ''; renderPreview(); syncSettings(); };
    previewImage.src = url;
    renderGallery(); renderPreview(); syncSettings();
    $('readyUploadStatus').textContent = `Ready to print: ${file.name} · ${(file.size / (1024 * 1024)).toFixed(1)} MB`;
    $('readyDesignControls').querySelector('label').textContent = 'Replace print-ready file';
  };
  const saveProductState = () => productStates.set(currentSlug, {
    artworks: [...art], activeId, mode: designMode, angle: productAngle, zoom: productZoom, color: currentColor,
    size: $('sizeSelect').value, quantity: $('quantity').value, back: backView, location: defaultLocation, view:currentView,
  });
  const switchProduct = slug => {
    const next = catalog[slug]; if (!next || slug === currentSlug) return;
    saveProductState(); currentSlug = slug; currentProduct = next;
    studio.dataset.product = slug; studio.dataset.price = next.price;
    $('productField').value = slug; $('selectedProductName').textContent = next.name;
    $('selectedProductDescription').textContent = next.description; $('editorTitle').textContent = `Customize ${next.name}`;
    const sizeSelect = $('sizeSelect'); sizeSelect.replaceChildren();
    next.sizes.forEach(size => {
      const option = document.createElement('option'); option.value = size.label; option.dataset.extra = size.extra;
      option.textContent = size.extra ? `${size.label} · +₹${size.extra}` : size.label; sizeSelect.append(option);
    });
    $('colorsFieldGroup').hidden = !next.has_color; const savedColor=productStates.get(slug)?.color;currentColor=next.has_color?(next.colors.includes(savedColor)?savedColor:(next.colors[0]||'white')):'white';
    locationSelect.replaceChildren();
    (next.print_areas || []).forEach(areaInfo => { const option = document.createElement('option'); option.value = areaInfo.id; option.textContent = areaInfo.label; locationSelect.append(option); });
    $('colorField').value = currentColor;
    renderColorSwatches(next);
    const state = productStates.get(slug);
    defaultLocation = state?.location || next.print_areas?.[0]?.id || 'front';
    currentView = state?.view || 'front';
    setDesignMode(state?.mode || 'create');
    art.splice(0, art.length, ...(state?.artworks || []).map(item=>({...item,view:item.view||state?.view||'front'}))); activeId = state?.activeId || null;
    productAngle = state?.angle || 0; productZoom = state?.zoom || 1; backView = state?.back || false;
    $('quantity').value = state?.quantity || 1;
    if (state?.size && next.sizes.some(size => size.label === state.size)) sizeSelect.value = state.size;
    currentColor = state?.color && next.colors.includes(state.color) ? state.color : currentColor; $('colorField').value = currentColor;renderColorSwatches(next);
    mockup.className = `mockup mock-${slug} color-${currentColor}`;
    productSelect.value = slug; clearError(); $('cartError').hidden = true;
    renderProductPhotos(next); renderGallery(); renderPreview(); syncSettings(); updatePrice(); applyProductView();
  };
  const openEditor = slug => {
    if (slug && slug !== currentSlug) switchProduct(slug);
    modal.hidden = false; modal.setAttribute('aria-hidden', 'false'); document.body.classList.add('modal-open');
    setTimeout(() => { $('editorClose').focus(); productPreview?.resize(); }, 0);
  };
  const closeEditor = () => {
    if (modal.classList.contains('standalone-editor')) { window.location.href = '/products'; return; }
    modal.hidden = true; modal.setAttribute('aria-hidden', 'true'); document.body.classList.remove('modal-open');
  };
  const closeDrawer = () => { drawer.hidden = true; };
  const openDrawer = payload => {
    $('cartDrawerMessage').textContent = payload.message;
    $('drawerTotal').textContent = `₹${Number(payload.cart_total).toLocaleString('en-IN')}`;
    const list = $('cartDrawerItems'); list.replaceChildren();
    payload.items.forEach(item => {
      const line = document.createElement('div'); line.className = 'drawer-line';
      const description = document.createElement('span'); description.textContent = `${item.name} × ${item.quantity}`;
      const detail = document.createElement('small'); detail.textContent = `${item.size} · ${item.color} · ${item.design_mode === 'ready' ? 'ready-made file' : `${item.artwork_count} photo(s)`}`; description.append(detail);
      const price = document.createElement('strong'); price.textContent = `₹${Number(item.line_total).toLocaleString('en-IN')}`;
      line.append(description, price); list.append(line);
    });
    drawer.hidden = false;
  };
  const applyColor = color => {
    if (!currentProduct.has_color || !['white','black','navy','red','blue','pink','yellow'].includes(color)) return;
    currentColor=color; $('colorField').value=color;
    document.querySelectorAll('.swatch').forEach(button=>button.classList.toggle('active',button.dataset.color===color));
    mockup.className=`mockup mock-${currentSlug} color-${color}`; productPreview?.setColor(color); saveProductState();
  };
  const createTextFile = (item, spec = item.textSpec) => new Promise(resolve => {
    const text=item.textSpec?.text || ''; if(!text){resolve(false);return;}
    const canvas=document.createElement('canvas'); canvas.width=1600; canvas.height=700;
    const ctx=canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height); ctx.fillStyle=({black:'#111111',white:'#ffffff',red:'#d62839',blue:'#2463c5',navy:'#17243b',purple:'#7139bd',green:'#16824c'})[spec.color]||'#17243b'; ctx.textAlign='center'; ctx.textBaseline='middle';
    let size=Math.max(30,Math.min(180,Number(spec.size)||100))*1.5; ctx.font=`${spec.italic?'italic ':''}${spec.bold?'700':'400'} ${size}px "${spec.font}", sans-serif`; while(ctx.measureText(text).width>1450&&size>32){size-=6;ctx.font=`${spec.italic?'italic ':''}${spec.bold?'700':'400'} ${size}px "${spec.font}", sans-serif`;}
    ctx.shadowColor='rgba(0,0,0,.16)';ctx.shadowBlur=8;ctx.fillText(text,800,350);
    canvas.toBlob(blob=>{if(!blob){resolve(false);return;}const oldUrl=item.url;item.file=new File([blob],'studio-text.png',{type:'image/png'});item.url=URL.createObjectURL(item.file);item.previewImage=new Image();item.previewImage.onload=updateThreeArtwork;item.previewImage.src=item.url;if(oldUrl)URL.revokeObjectURL(oldUrl);renderGallery();renderPreview();syncSettings();saveProductState();resolve(true);},'image/png');
  });
  const addTextArtwork = async (text, color='navy', font='Arial') => {
    if (designMode !== 'create' || !text.trim()) return false;
    const before = new Set(art.map(item=>item.id));
    const spec={text,color,font,size:Number($('textSizeInput').value)||100,bold:$('textBoldInput').checked,italic:$('textItalicInput').checked};
    const canvas=document.createElement('canvas'); canvas.width=1600; canvas.height=700; const ctx=canvas.getContext('2d');
    ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle=({black:'#111111',white:'#ffffff',red:'#d62839',blue:'#2463c5',navy:'#17243b',purple:'#7139bd',green:'#16824c'})[color]||'#17243b';ctx.textAlign='center';ctx.textBaseline='middle';
    let size=spec.size*1.5;ctx.font=`${spec.italic?'italic ':''}${spec.bold?'700':'400'} ${size}px "${font}", sans-serif`;while(ctx.measureText(text).width>1450&&size>32){size-=6;ctx.font=`${spec.italic?'italic ':''}${spec.bold?'700':'400'} ${size}px "${font}", sans-serif`;}
    ctx.shadowColor='rgba(0,0,0,.16)';ctx.shadowBlur=8;ctx.fillText(text,800,350);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)return false;
    addFiles([new File([blob],'studio-text.png',{type:'image/png'})]);
    const item=art.find(entry=>!before.has(entry.id)); if(!item)return false;
    activeId=item.id;item.textSpec=spec;renderGallery();renderPreview();syncSettings();saveProductState();return true;
  };
  const setTextStyle = async (color=null,font=null) => { const item=selected(); if(!item?.textSpec)return false; if(color&&['black','white','red','blue','navy','purple','green'].includes(color))$('textColorSelect').value=color;if(font&&['Arial','Georgia','Impact','Trebuchet MS'].includes(font))$('textFontSelect').value=font;item.textSpec={...item.textSpec,font:$('textFontSelect').value,color:$('textColorSelect').value,size:Number($('textSizeInput').value),bold:$('textBoldInput').checked,italic:$('textItalicInput').checked};return createTextFile(item); };
  const captureHistory = () => ({slug:currentSlug,art:art.map(item=>({...item,textSpec:item.textSpec?{...item.textSpec}:null})),activeId,location:defaultLocation,view:currentView,color:currentColor,angle:productAngle,zoom:productZoom,back:backView,mode:designMode,size:$('sizeSelect').value,quantity:$('quantity').value});
  const pushHistory = () => {undoStack.push(captureHistory());if(undoStack.length>30)undoStack.shift();redoStack.splice(0);};
  const restoreHistory = state => {
    const priorIds=new Set(state.art.map(item=>item.id));art.filter(item=>!priorIds.has(item.id)).forEach(item=>URL.revokeObjectURL(item.url));
    if(currentSlug!==state.slug)switchProduct(state.slug);
    art.forEach(item=>URL.revokeObjectURL(item.url));
    art.splice(0,art.length,...state.art.map(item=>{const copy={...item,view:item.view||state.view||'front',url:URL.createObjectURL(item.file)};copy.previewImage=new Image();copy.previewImage.onload=updateThreeArtwork;copy.previewImage.src=copy.url;return copy;}));activeId=state.activeId;defaultLocation=state.location;currentView=state.view||'front';currentColor=state.color;productAngle=state.angle;productZoom=state.zoom;backView=state.back;
    $('quantity').value=state.quantity; if([...$('sizeSelect').options].some(option=>option.value===state.size))$('sizeSelect').value=state.size;
    designMode=state.mode;$('designModeField').value=designMode;$('createDesignControls').hidden=designMode==='ready';$('readyDesignControls').hidden=designMode!=='ready';studio.classList.toggle('ready-design-mode',designMode==='ready');$('colorField').value=currentColor;mockup.className=`mockup mock-${currentSlug} color-${currentColor}`;
    locationSelect.value=defaultLocation;renderProductPhotos(currentProduct);renderGallery();renderPreview();syncSettings();updatePrice();applyProductView();productPreview?.setColor(currentColor);saveProductState();
  };
  const undoDesign = () => {if(!undoStack.length)return;redoStack.push(captureHistory());restoreHistory(undoStack.pop());};
  const redoDesign = () => {if(!redoStack.length)return;undoStack.push(captureHistory());restoreHistory(redoStack.pop());};
  const moveArtworkLayer = delta => {
    const index=art.findIndex(item=>item.id===activeId), next=index+delta;
    if(index<0||next<0||next>=art.length)return;
    [art[index],art[next]]=[art[next],art[index]];renderGallery();renderPreview();syncSettings();saveProductState();
  };
  const executeAssistantAction = async action => {
    if(action.action==='select_product'&&catalog[action.product]) switchProduct(action.product);
    else if(action.action==='set_location'&&setPrintLocation(action.location)){renderPreview();syncSettings();saveProductState();}
    else if(['select_artwork','add_uploaded_image'].includes(action.action)&&art[action.index]){activeId=art[action.index].id;setPrintLocation(art[action.index].location,false);renderGallery();renderPreview();syncSettings();}
    else if(['place_artwork','move_artwork'].includes(action.action)&&selected()) {
      const item=selected(), rect=area.getBoundingClientRect();
      if(action.action==='move_artwork'){item.x=action.x*rect.width;item.y=action.y*rect.height;}
      else {if(action.placement==='left_chest')item.x=-rect.width*.12;else if(action.placement==='right_chest')item.x=rect.width*.12;else item.x=0;item.y=action.placement==='upper_center'?-rect.height*.12:action.placement==='lower_center'?rect.height*.12:0;}
      applyArtTransform(item);syncSettings();saveProductState();
    } else if(action.action==='resize_artwork'&&selected()){$('zoomRange').value=Math.round(action.scale*100);updateArtwork();}
    else if(action.action==='rotate_artwork'&&selected()){$('rotateRange').value=action.angle;updateArtwork();}
    else if(action.action==='set_color') applyColor(action.color);
    else if(action.action==='add_text') await addTextArtwork(action.text||'',action.color||'navy',action.font||'Arial');
    else if(action.action==='set_text_style') await setTextStyle(action.color,action.font);
    else if(action.action==='set_view'&&currentSlug==='tshirt') setTshirtView(action.view);
    else if(action.action==='undo') undoDesign();
    else if(action.action==='redo') redoDesign();
    else if(action.action==='reset_design') {clearArt();productAngle=0;productZoom=1;backView=false;currentColor='white';$('colorField').value='white';mockup.className=`mockup mock-${currentSlug} color-white`;applyProductView();productPreview?.setColor('white');saveProductState();}
    else if(action.action==='quality_check') {
      const image=selected()?.previewImage;
      if(image?.naturalWidth){const short=Math.min(image.naturalWidth,image.naturalHeight);appendAssistantMessage('assistant',`Selected image: ${image.naturalWidth} x ${image.naturalHeight} pixels. ${short<1200?'It may print soft at larger sizes; upload a higher-resolution original.':'Pixel dimensions look suitable for a standard print preview.'} The original file is preserved.`);}
      else appendAssistantMessage('assistant','Upload an image first for a resolution check. I do not upscale or alter your original artwork.');
    }
  };
  const appendAssistantMessage = (role,text) => {const node=document.createElement('p');node.className=`assistant-message assistant-${role}`;node.textContent=text;$('assistantMessages').append(node);$('assistantMessages').scrollTop=$('assistantMessages').scrollHeight;};
  const askAssistant = async () => {
    const message=$('assistantInput').value.trim();if(!message)return;
    $('assistantInput').value='';$('assistantSend').disabled=true;$('assistantError').hidden=true;appendAssistantMessage('user',message);
    const image=selected()?.previewImage;
    try {
      const response=await fetch('/api/design-assistant',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-CSRF-Token':document.querySelector('meta[name=csrf-token]')?.content||''},body:JSON.stringify({message,product:currentSlug,location:defaultLocation,image_count:art.length,image_width:image?.naturalWidth||null,image_height:image?.naturalHeight||null,has_text_selection:Boolean(selected()?.textSpec),conversation:[...$('assistantMessages').querySelectorAll('.assistant-message')].slice(0,-1).slice(-8).map(node=>({role:node.classList.contains('assistant-user')?'user':'assistant',content:node.textContent}))})});
      const result=await response.json();if(!response.ok||!result.success)throw new Error(result.message||'Assistant unavailable.');
      $('assistantMode').textContent='CONNECTED AI';
      const actions=result.actions||[];if(actions.length&&!actions.some(action=>['undo','redo'].includes(action.action)))pushHistory();
      for(const action of actions)await executeAssistantAction(action);
      appendAssistantMessage('assistant',result.reply);
    } catch(error) {$('assistantError').textContent=error.message;$('assistantError').hidden=false;}
    finally {$('assistantSend').disabled=false;$('assistantInput').focus();}
  };

  input.addEventListener('change', () => addFiles(input.files));
  multipleInput.addEventListener('change', () => addFiles(multipleInput.files));
  readyInput.addEventListener('change', () => addReadyFile(readyInput.files[0]));
  locationSelect.addEventListener('change', () => {setPrintLocation(locationSelect.value);renderPreview();syncSettings();saveProductState();});
  $('assistantSend').addEventListener('click', askAssistant);
  $('assistantUploadImage').addEventListener('click',()=>{if(designMode==='create')multipleInput.click();else appendAssistantMessage('assistant','Switch to Create Your Own Design to add individual image layers.');});
  $('assistantUndo').addEventListener('click',undoDesign);$('assistantRedo').addEventListener('click',redoDesign);
  $('designUndo').addEventListener('click',undoDesign);$('designRedo').addEventListener('click',redoDesign);
  $('clearDesignButton').addEventListener('click',()=>{if(!art.length||!confirm('Clear all uploaded artwork and text from this design?'))return;pushHistory();clearArt();saveProductState();});
  $('assistantClear').addEventListener('click',()=>{$('assistantMessages').replaceChildren();appendAssistantMessage('assistant','Conversation cleared. Your current design is unchanged.');$('assistantError').hidden=true;});
  document.querySelectorAll('[data-assistant-prompt]').forEach(button=>button.addEventListener('click',()=>{$('assistantInput').value=button.dataset.assistantPrompt;askAssistant();}));
  $('assistantInput').addEventListener('keydown', event=>{if(event.key==='Enter'){event.preventDefault();askAssistant();}});
  $('addTextButton').addEventListener('click',()=>addTextArtwork($('designTextInput').value).then(added=>{if(added)$('designTextInput').value='';}));
  ['textFontSelect','textSizeInput','textColorSelect','textBoldInput','textItalicInput'].forEach(id=>$(id).addEventListener('change',()=>{if(selected()?.textSpec)setTextStyle();}));
  $('layerUpButton').addEventListener('click',()=>moveArtworkLayer(1));
  $('layerDownButton').addEventListener('click',()=>moveArtworkLayer(-1));
  $('createModeButton').addEventListener('click', () => {
    if (designMode === 'create') return;
    pushHistory(); setDesignMode('create'); saveProductState();
  });
  $('readyModeButton').addEventListener('click', () => {
    if (designMode === 'ready') return;
    pushHistory(); setDesignMode('ready'); saveProductState();
  });
  $('multipleBtn').addEventListener('click', () => multipleInput.click());
  document.querySelectorAll('[data-customize]').forEach(button => button.addEventListener('click', () => openEditor(button.dataset.customize)));
  document.querySelectorAll('[data-ready-design]').forEach(button => button.addEventListener('click', () => {
    openEditor(button.dataset.readyDesign); setDesignMode('ready'); readyInput.click();
  }));
  productSelect.addEventListener('change', () => switchProduct(productSelect.value));
  $('editorClose').addEventListener('click', closeEditor);
  modal.addEventListener('click', event => { if (event.target === modal) closeEditor(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') { if (!drawer.hidden) closeDrawer(); else if (!modal.hidden) closeEditor(); } });
  $('cartDrawerClose').addEventListener('click', closeDrawer); $('continueShopping').addEventListener('click', closeDrawer);
  drawer.addEventListener('click', event => { if (event.target === drawer) closeDrawer(); });

  $('removeBtn').addEventListener('click', () => {
    const item = selected(); if (!item) return;
    URL.revokeObjectURL(item.url); const index = art.findIndex(entry => entry.id === item.id); art.splice(index, 1);
    activeId = art.length ? art[Math.min(index, art.length - 1)].id : null;
    area.querySelector(`[data-art-id="${item.id}"]`)?.remove(); renderGallery(); syncSettings(); updateThreeArtwork(); saveProductState();
  });
  $('resetBtn').addEventListener('click', () => {
    const item = selected(); if (!item) return;
    item.scale = 1; item.angle = 0; item.x = 0; item.y = 0; applyArtTransform(item); syncSettings(); updateThreeArtwork(); saveProductState();
  });
  const updateArtwork = () => {
    const item = selected(); if (!item) return;
    item.scale = Number($('zoomRange').value) / 100; item.angle = Number($('rotateRange').value);
    applyArtTransform(item); syncSettings(); updateThreeArtwork(); saveProductState();
  };
  $('zoomRange').addEventListener('input', updateArtwork); $('rotateRange').addEventListener('input', updateArtwork);
  $('zoomIn').addEventListener('click', () => { if (selected()) { $('zoomRange').value = Math.min(220, Number($('zoomRange').value) + 10); updateArtwork(); } });
  $('zoomOut').addEventListener('click', () => { if (selected()) { $('zoomRange').value = Math.max(30, Number($('zoomRange').value) - 10); updateArtwork(); } });
  const clampDrag = (value, span) => Math.max(-span * .35, Math.min(span * .35, value));
  area.addEventListener('pointerdown', event => {
    if (designMode === 'ready') return;
    const item = selected(); if (!item || event.target === $('printHint')) return;
    area.setPointerCapture(event.pointerId); item.drag = {px: event.clientX, py: event.clientY, x: item.x, y: item.y};
  });
  area.addEventListener('pointermove', event => {
    if (designMode === 'ready') return;
    const item = selected(); if (!item?.drag) return;
    const rect = area.getBoundingClientRect(); item.x = clampDrag(item.drag.x + event.clientX - item.drag.px, rect.width);
    item.y = clampDrag(item.drag.y + event.clientY - item.drag.py, rect.height); applyArtTransform(item); syncSettings(); saveProductState();
  });
  ['pointerup', 'pointercancel'].forEach(type => area.addEventListener(type, () => { const item = selected(); if (item) delete item.drag; }));
  previewStage.addEventListener('pointerdown', event => {
    if (previewMode === 'photo') return;
    if (event.target.closest('#printArea, .product-view-controls, .stage-caption')) return;
    if (designMode !== 'ready' && productPreview && selected() && productPreview.beginArtworkDrag(event, selected(), area.getBoundingClientRect())) {
      turning = false; previewStage.setPointerCapture(event.pointerId); return;
    }
    turning = true; turnStart = {x: event.clientX, angle: productAngle}; previewStage.setPointerCapture(event.pointerId);
  });
  previewStage.addEventListener('pointermove', event => {
    if (productPreview?.artDrag) {
      const item = selected(), position = item && productPreview.moveArtworkDrag(event, area.getBoundingClientRect());
      if (item && position) { item.x = position.x; item.y = position.y; applyArtTransform(item); syncSettings(); saveProductState(); }
      return;
    }
    if (!turning || !turnStart) return;
    productAngle = turnStart.angle + (event.clientX - turnStart.x) * .75; applyProductView(false); saveProductState();
  });
  ['pointerup', 'pointercancel'].forEach(type => previewStage.addEventListener(type, () => { turning = false; turnStart = null; productPreview?.endArtworkDrag(); applyProductView(true); }));
  $('productZoomIn').addEventListener('click', () => { productZoom = Math.min(1.3, productZoom + .08); applyProductView(); saveProductState(); });
  $('productZoomOut').addEventListener('click', () => { productZoom = Math.max(.7, productZoom - .08); applyProductView(); saveProductState(); });
  $('productViewReset').addEventListener('click', () => { productAngle = 0; productZoom = 1; backView = false; applyProductView(); saveProductState(); });
  $('productRotate360').addEventListener('click', () => { productAngle += productAngle >= 360 ? -360 : 360; applyProductView(); saveProductState(); });
  $('tshirtFrontView').addEventListener('click',()=>setTshirtView('front'));
  $('tshirtBackView').addEventListener('click',()=>setTshirtView('back'));
  $('tshirtSideView').addEventListener('click',()=>setTshirtView('right'));
  $('tshirtLeftView').addEventListener('click',()=>{if(currentSlug==='tshirt')activateProductView('left');});
  $('tshirtRightView').addEventListener('click',()=>{if(currentSlug==='tshirt')activateProductView('right');});
  document.querySelectorAll('.swatch').forEach(button => button.addEventListener('click', () => applyColor(button.dataset.color)));
  const updatePrice = () => {
    const sizeExtra = Number($('sizeSelect').selectedOptions[0]?.dataset.extra || 0);
    const quantity = Math.max(1, Math.min(99, Number($('quantity').value || 1)));
    const undiscounted=currentProduct.price+sizeExtra+Number(currentProduct.printing_charge??studio.dataset.printing)+Number(currentProduct.design_charge||0);const discount=undiscounted*Number(currentProduct.discount_percent||0)/100;const total=(undiscounted-discount)*quantity;
    $('basePrice').textContent = `₹${currentProduct.price.toLocaleString('en-IN')}`;
    if($('printingPrice'))$('printingPrice').textContent=String.fromCharCode(8377)+Number(currentProduct.printing_charge??studio.dataset.printing); $('sizePrice').textContent = String.fromCharCode(8377)+sizeExtra; if($('designPrice')) $('designPrice').textContent=String.fromCharCode(8377)+Number(currentProduct.design_charge||0); if($('discountPrice')) $('discountPrice').textContent='-'+String.fromCharCode(8377)+discount.toLocaleString('en-IN'); $('totalPrice').textContent = String.fromCharCode(8377)+total.toLocaleString('en-IN');
  };
  $('sizeSelect').addEventListener('change', () => { updatePrice(); saveProductState(); });
  $('quantity').addEventListener('input', () => { updatePrice(); saveProductState(); });
  $('qtyMinus').addEventListener('click', () => { const q = $('quantity'); q.value = Math.max(1, Number(q.value) - 1); updatePrice(); saveProductState(); });
  $('qtyPlus').addEventListener('click', () => { const q = $('quantity'); q.value = Math.min(99, Number(q.value) + 1); updatePrice(); saveProductState(); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); clearError(); $('cartError').hidden = true;
    if (!art.length) {
      const message = designMode === 'ready' ? 'Choose a completed design file before adding this product to your cart.' : 'Add at least one PNG or JPG photo before adding the product to your cart.';
      if (designMode === 'ready') { $('readyUploadError').textContent = message; $('readyUploadError').hidden = false; } else showError(message);
      return;
    }
    syncSettings(); const submit = $('addCartBtn'); submit.disabled = true; submit.querySelector('span').textContent = 'SAVING DESIGN…';
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'}});
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.success !== true) throw new Error(payload?.message || `Unable to save this design (HTTP ${response.status}). Please try again.`);
      const savedItem = payload.items?.[payload.items.length - 1];
      if (!Array.isArray(savedItem?.artworks) || savedItem.artworks.length !== art.length) throw new Error('The server did not return the saved image details. Your preview is unchanged; please try again.');
      savedItem.artworks.forEach((saved, index) => {
        if (typeof saved.filename !== 'string' || !saved.filename || typeof saved.image_url !== 'string' || saved.image_url === 'undefined') throw new Error('The server returned an invalid image URL. Your preview is unchanged; please try again.');
        const imageUrl = new URL(saved.image_url, window.location.href);
        if (imageUrl.origin !== window.location.origin || !imageUrl.pathname.startsWith('/uploads/')) throw new Error('The server returned an unsafe image URL. Your preview is unchanged; please try again.');
        art[index].uploadedUrl = imageUrl.href; art[index].uploadedFilename = saved.filename;
      });
      renderGallery(); renderPreview();
      openDrawer(payload);
    } catch (error) {
      $('cartError').textContent = error.message; $('cartError').hidden = false;
    } finally {
      submit.disabled = false; submit.querySelector('span').textContent = 'ADD DESIGN TO CART';
    }
  });
  if (currentProduct) {
    renderProductPhotos(currentProduct);
    productSelect.value = currentSlug; mockup.className = `mockup mock-${currentSlug} color-${currentColor}`;
    locationSelect.replaceChildren();
    (currentProduct.print_areas || []).forEach(areaInfo => { const option = document.createElement('option'); option.value = areaInfo.id; option.textContent = areaInfo.label; locationSelect.append(option); });
    locationSelect.value = defaultLocation;renderColorSwatches(currentProduct);
    $('bannerReference').hidden = currentSlug !== 'banner'; previewStage.classList.toggle('banner-preview-stage', currentSlug === 'banner');
  }
  syncSettings(); updatePrice(); renderPreview(); applyProductView(); mountThreePreview(); setPreviewMode(previewMode);
})();
