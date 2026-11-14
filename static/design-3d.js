import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

const colors = {white: 0xf1f2f5, black: 0x1b1d25, navy: 0x283853, red: 0xc93855, blue: 0x3477c7, pink: 0xe99fba, yellow: 0xe5c45d};
const productLocations = {
  tshirt:{front_center:.82/.9,back_center:.82/.9,left_chest:.28/.3,right_chest:.28/.3,left_sleeve:.24/.32,right_sleeve:.24/.32},
  mug:{wrap:2},pillow:{front:1.18},banner:{full_front:2.59/1.34},frame:{photo:1.36/1.75},
  phonecase:{back:.91/1.25},poster:{full_front:1.7/2.22},gift:{front:.72/.68}
};
const roundedRect = (width, height, radius) => {
  const shape = new THREE.Shape(), x = -width / 2, y = -height / 2;
  shape.moveTo(x + radius, y); shape.lineTo(x + width - radius, y); shape.quadraticCurveTo(x + width, y, x + width, y + radius);
  shape.lineTo(x + width, y + height - radius); shape.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  shape.lineTo(x + radius, y + height); shape.quadraticCurveTo(x, y + height, x, y + height - radius);
  shape.lineTo(x, y + radius); shape.quadraticCurveTo(x, y, x + radius, y); return shape;
};

class ProductPreview {
  constructor(container, stage, slug, color, assetConfig = null) {
    this.container = container; this.stage = stage; this.slug = slug; this.color = color;
    this.group = new THREE.Group(); this.angle = 0; this.zoom = 1; this.back = false; this.targetRotation=0; this.targetScale=1;
    this.fabricTextures = new Map();
    this.detailTextures = new Map();
    this.regionCanvases = new Map(); this.regionTextures = new Map(); this.assetSurfaceMeshes = new Map();
    this.raycaster = new THREE.Raycaster(); this.pointer = new THREE.Vector2(); this.artSurface = null; this.artSurfaces = []; this.artDrag = null;
    this.scene = new THREE.Scene(); this.scene.add(this.group);
    this.camera = new THREE.PerspectiveCamera(34, 1, .1, 100); this.camera.position.set(3.2, 2.4, 5.8); this.camera.lookAt(0, 0, 0);
    this.renderer = new THREE.WebGLRenderer({alpha: true, antialias: true, powerPreference: 'low-power'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.append(this.renderer.domElement);
    this.scene.add(new THREE.HemisphereLight(0xdbe8ff, 0x303347, 2.15));
    const key = new THREE.DirectionalLight(0xffffff, 3.0); key.position.set(-3.5, 5, 5); key.castShadow = true; this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x8fdcf7, 1.45); rim.position.set(4, 2, -4); this.scene.add(rim);
    const fill = new THREE.PointLight(0x9379ff, 35, 12); fill.position.set(-3, -1, 3); this.scene.add(fill);
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(5.5, 4), new THREE.ShadowMaterial({opacity: .22}));
    this.shadow=shadow;
    shadow.rotation.x = -Math.PI / 2; shadow.position.y = -1.7; shadow.receiveShadow = true; this.scene.add(shadow);
    this.canvas = document.createElement('canvas'); this.texture = new THREE.CanvasTexture(this.canvas); this.texture.colorSpace = THREE.SRGBColorSpace;
    this.gltfLoader = new GLTFLoader(); this.assetLoadingSlug = null; this.assetMode = false; this.assetConfig = assetConfig;
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(stage);
    this.buildProduct(slug, color); this.resize(); this.animate(); this.loadConfiguredAsset(slug);
  }
  resize() {
    const rect = this.stage.getBoundingClientRect(); if (!rect.width || !rect.height) return;
    this.camera.aspect = rect.width / rect.height; this.camera.updateProjectionMatrix();
    this.renderer.setSize(rect.width, rect.height, false);
  }
  clearGroup() {
    this.assetOwnedTextures?.forEach(texture=>texture.dispose()); this.assetOwnedTextures?.clear();
    for (const child of [...this.group.children]) {
      child.traverse?.(part => { if (part.geometry) part.geometry.dispose(); if (part.material) (Array.isArray(part.material) ? part.material : [part.material]).forEach(material => material.dispose()); });
      this.group.remove(child);
    }
  }
  material(color, extra = {}) { return new THREE.MeshStandardMaterial({color, roughness: .78, metalness: .02, ...extra}); }
  fabricTexture() {
    if (this.fabricTextures.has('weave')) return this.fabricTextures.get('weave');
    const canvas=document.createElement('canvas'); canvas.width=canvas.height=128;
    const ctx=canvas.getContext('2d'); ctx.fillStyle='#f4f4f4'; ctx.fillRect(0,0,128,128);
    for(let i=0;i<128;i+=4){ctx.strokeStyle=i%8?'rgba(60,68,78,.07)':'rgba(255,255,255,.65)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(i,0);ctx.lineTo(i,128);ctx.stroke();ctx.strokeStyle=i%8?'rgba(60,68,78,.06)':'rgba(255,255,255,.6)';ctx.beginPath();ctx.moveTo(0,i);ctx.lineTo(128,i);ctx.stroke();}
    const texture=new THREE.CanvasTexture(canvas); texture.wrapS=texture.wrapT=THREE.RepeatWrapping; texture.repeat.set(4,4); texture.colorSpace=THREE.SRGBColorSpace;
    this.fabricTextures.set('weave',texture); return texture;
  }
  detailTexture(kind) {
    if(this.detailTextures.has(kind)) return this.detailTextures.get(kind);
    const canvas=document.createElement('canvas'); canvas.width=canvas.height=256;
    const ctx=canvas.getContext('2d'); ctx.fillStyle='#eeeeeb'; ctx.fillRect(0,0,256,256);
    let seed=kind==='wood'?37:kind==='paper'?71:kind==='flex'?109:151;
    const random=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
    if(kind==='wood') {
      for(let i=0;i<180;i++){const y=random()*256, bend=random()*4-2;ctx.strokeStyle=`rgba(72,45,23,${.025+random()*.075})`;ctx.lineWidth=.4+random()*1.5;ctx.beginPath();ctx.moveTo(0,y);ctx.bezierCurveTo(75,y+bend,175,y-bend,256,y+random()*3-1.5);ctx.stroke();}
    } else if(kind==='flex') {
      for(let i=0;i<256;i+=3){ctx.strokeStyle=i%6?'rgba(75,83,91,.035)':'rgba(255,255,255,.3)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(i,0);ctx.lineTo(i,256);ctx.stroke();ctx.beginPath();ctx.moveTo(0,i);ctx.lineTo(256,i);ctx.stroke();}
    } else {
      for(let i=0;i<2400;i++){const x=random()*256,y=random()*256;ctx.fillStyle=kind==='paper'?`rgba(110,102,83,${random()*.08})`:`rgba(100,78,45,${random()*.05})`;ctx.fillRect(x,y,random()*1.5+.3,random()*2+.3);}
    }
    const texture=new THREE.CanvasTexture(canvas); texture.wrapS=texture.wrapT=THREE.RepeatWrapping; texture.repeat.set(kind==='wood'?1.5:3,kind==='wood'?1:3); texture.colorSpace=THREE.SRGBColorSpace;
    this.detailTextures.set(kind,texture); return texture;
  }
  configureProductTextures(slug) {
    const definitions=productLocations[slug]||{front:1};
    for(const [location,ratio] of Object.entries(definitions)) {
      let canvas=this.regionCanvases.get(location), texture=this.regionTextures.get(location);
      if(!canvas) { canvas=document.createElement('canvas'); this.regionCanvases.set(location,canvas); }
      canvas.width=1024; canvas.height=Math.max(1,Math.round(1024/ratio));
      if(!texture) { texture=new THREE.CanvasTexture(canvas); texture.colorSpace=THREE.SRGBColorSpace; this.regionTextures.set(location,texture); }
      texture.needsUpdate=true;
    }
    const primary=Object.keys(definitions)[0]; this.canvas=this.regionCanvases.get(primary); this.texture=this.regionTextures.get(primary);
  }
  textureFor(location) { return this.regionTextures.get(location)||this.texture; }
  stitch(points,color=0x858b96,radius=.006) {
    const curve=new THREE.CatmullRomCurve3(points.map(point=>new THREE.Vector3(...point)));
    return this.addMesh(new THREE.TubeGeometry(curve,Math.max(20,points.length*8),radius,6,false),new THREE.MeshStandardMaterial({color,roughness:.92}));
  }
  addMesh(geometry, material, x=0, y=0, z=0, parent=this.group) {
    const mesh = new THREE.Mesh(geometry, material); mesh.position.set(x,y,z); mesh.castShadow = true; mesh.receiveShadow = true; parent.add(mesh); return mesh;
  }
  extruded(shape, depth, bevel=.04) {
    const geometry = new THREE.ExtrudeGeometry(shape, {depth, bevelEnabled: true, bevelSegments: 4, steps: 1, bevelSize: bevel, bevelThickness: bevel});
    geometry.translate(0, 0, -depth/2); return geometry;
  }
  printPlane(parent, x, y, z, width, height, back=false, region='front_center', rotation=0) {
    const material = new THREE.MeshStandardMaterial({map: this.textureFor(region), transparent: true, alphaTest:.005, roughness: .86, side: THREE.DoubleSide, depthWrite: false});
    const mesh = this.addMesh(new THREE.PlaneGeometry(width, height), material, x, y, z, parent);
    if (back) mesh.rotation.y = Math.PI;
    mesh.rotation.z=rotation; mesh.userData.printSurface = true; mesh.userData.printRegion=region; this.artSurface = mesh; this.artSurfaces.push(mesh);
    mesh.renderOrder = 4; return mesh;
  }
  curvedPlane(parent, width, height, material, segments=22, depth=.035) {
    const geometry = new THREE.PlaneGeometry(width, height, segments, Math.max(4, Math.floor(segments*.55)));
    const points = geometry.attributes.position;
    for (let i=0; i<points.count; i++) { const x=points.getX(i), y=points.getY(i); points.setZ(i, depth*Math.sin((x/width)*Math.PI*1.7) + .012*Math.sin((y/height)*Math.PI*3)); }
    geometry.computeVertexNormals(); return this.addMesh(geometry, material, 0, 0, 0, parent);
  }
  shirtRelief(x,y) {
    return .035*Math.pow(x/1.12,2)+.014*Math.pow(y/1.28,2)+.006*Math.sin(x*5+y*3)*Math.sin(y*7);
  }
  shirtPrintPanel(parent,cx,cy,width,height,region,back=false,rotation=0) {
    const material=new THREE.MeshStandardMaterial({map:this.textureFor(region),transparent:true,alphaTest:.003,roughness:.88,side:THREE.DoubleSide,depthWrite:false});
    const geometry=new THREE.PlaneGeometry(width,height,28,28), points=geometry.attributes.position;
    for(let i=0;i<points.count;i++) {
      let x=points.getX(i), y=points.getY(i);
      const rx=x*Math.cos(rotation)-y*Math.sin(rotation), ry=x*Math.sin(rotation)+y*Math.cos(rotation);
      x=rx+cx; y=ry+cy;
      // Keep the UV print panel beyond the shirt's bevel thickness while
      // following its relief, so the body does not occlude the artwork.
      // The back panel is rotated 180° below, which flips its local +Z onto
      // the rear of the shirt. Keep both panels' local offsets positive.
      points.setZ(i,.13+this.shirtRelief(x,y));
    }
    geometry.computeVertexNormals();
    const mesh=this.addMesh(geometry,material,cx,cy,0,parent);
    mesh.rotation.y=back?Math.PI:0; mesh.rotation.z=rotation;
    mesh.userData.printSurface=true; mesh.userData.printRegion=region; mesh.renderOrder=4;
    this.artSurface=mesh; this.artSurfaces.push(mesh); return mesh;
  }
  buildProduct(slug=this.slug, color=this.color) {
    this.clearGroup(); this.artSurface=null; this.artSurfaces=[]; this.assetSurfaceMeshes.clear(); this.assetMode=false; this.slug=slug; this.color=color; this.group.scale.setScalar(1); this.group.position.set(0,0,0);
    this.configureProductTextures(slug);
    const shirt = colors[color] || colors.white;
    if (slug === 'tshirt') {
      const s = new THREE.Shape();
      s.moveTo(-.43,1.05); s.lineTo(-.72,1.27); s.lineTo(-1.12,.96); s.lineTo(-.83,.37); s.lineTo(-.61,.50); s.lineTo(-.68,-1.15); s.quadraticCurveTo(0,-1.28,.68,-1.15); s.lineTo(.61,.50); s.lineTo(.83,.37); s.lineTo(1.12,.96); s.lineTo(.72,1.27); s.lineTo(.43,1.05); s.quadraticCurveTo(0,.82,-.43,1.05);
      const weave=this.fabricTexture();
      const body=this.addMesh(this.extruded(s,.14,.045), this.material(shirt,{map:weave,bumpMap:weave,bumpScale:.012,roughness:.97}));
      const bodyPositions=body.geometry.attributes.position;
      for(let i=0;i<bodyPositions.count;i++) { const x=bodyPositions.getX(i),y=bodyPositions.getY(i),z=bodyPositions.getZ(i); bodyPositions.setZ(i,z+Math.sign(z)*this.shirtRelief(x,y)); }
      bodyPositions.needsUpdate=true;
      body.geometry.computeVertexNormals();
      const collar=this.addMesh(new THREE.TorusGeometry(.235,.044,12,48),this.material(shirt===colors.white?0xc8ccd3:shirt*.72,{roughness:.9}),0,.98,.105);
      collar.scale.set(1.16,.63,1);
      const seamMat=new THREE.LineBasicMaterial({color: shirt===colors.white?0xb5bac4:0xffffff,transparent:true,opacity:.32});
      const seamPts=[new THREE.Vector3(-.64,-.92,.078),new THREE.Vector3(-.61,.43,.078),new THREE.Vector3(-.83,.32,.078),new THREE.Vector3(-1.03,.92,.078),new THREE.Vector3(-.72,1.16,.078),new THREE.Vector3(-.4,.97,.078),new THREE.Vector3(0,.85,.078),new THREE.Vector3(.4,.97,.078),new THREE.Vector3(.72,1.16,.078),new THREE.Vector3(1.03,.92,.078),new THREE.Vector3(.83,.32,.078),new THREE.Vector3(.61,.43,.078),new THREE.Vector3(.64,-.92,.078)];
      this.group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(seamPts),seamMat));
      for(const side of [-1,1]) {
        this.stitch([[side*.78,.35,.081],[side*.84,.48,.081],[side*.91,.68,.081],[side*1.02,.91,.081]],shirt===colors.white?0x9da4af:0xd6dce7,.004);
      }
      this.stitch([[-.62,-.99,.081],[-.32,-1.035,.081],[0,-1.045,.081],[.32,-1.035,.081],[.62,-.99,.081]],shirt===colors.white?0xb1b7c0:0xcbd1db,.0035);
      const creaseMat=new THREE.MeshStandardMaterial({color:shirt===colors.white?0xa8afbb:0x202635,transparent:true,opacity:.12,roughness:.98});
      for(const side of [-1,1]) {
        const crease=new THREE.CatmullRomCurve3([[side*.55,.43,.082],[side*.47,.13,.081],[side*.53,-.2,.081],[side*.48,-.56,.081]].map(point=>new THREE.Vector3(...point)));
        this.addMesh(new THREE.TubeGeometry(crease,28,.012,7,false),creaseMat);
      }
      this.shirtPrintPanel(this.group,0,.05,.82,.9,'front_center'); this.shirtPrintPanel(this.group,0,.05,.82,.9,'back_center',true);
      this.shirtPrintPanel(this.group,-.28,.48,.28,.3,'left_chest'); this.shirtPrintPanel(this.group,.28,.48,.28,.3,'right_chest');
      this.shirtPrintPanel(this.group,-.88,.79,.24,.3,'left_sleeve',false,-.38); this.shirtPrintPanel(this.group,.88,.79,.24,.3,'right_sleeve',false,.38);
    } else if (slug === 'mug') {
      const mugMat=new THREE.MeshPhysicalMaterial({color:0xf6f7fa,map:this.textureFor('wrap'),transparent:true,alphaTest:.005,roughness:.19,metalness:.025,clearcoat:.72,clearcoatRoughness:.16,side:THREE.DoubleSide});
      this.addMesh(new THREE.CylinderGeometry(.67,.62,1.25,64,2,true),mugMat,0,0,0);
      this.addMesh(new THREE.CircleGeometry(.62,64),this.material(0xe3e5e9),0,-.625,0).rotation.x=Math.PI/2;
      const ceramic=new THREE.MeshPhysicalMaterial({color:0xf4f5f8,roughness:.24,metalness:.015,clearcoat:.48,clearcoatRoughness:.2,side:THREE.DoubleSide});
      this.addMesh(new THREE.CylinderGeometry(.54,.50,1.17,56,2,true),ceramic,0,.015,0);
      this.addMesh(new THREE.CircleGeometry(.50,56),ceramic,0,-.57,0).rotation.x=-Math.PI/2;
      const rim=this.addMesh(new THREE.TorusGeometry(.645,.027,16,64),ceramic,0,.622,0); rim.rotation.x=Math.PI/2;
      this.artSurface=this.group.children.find(child=>child.geometry?.type==='CylinderGeometry'); this.artSurface.userData.printSurface=true; this.artSurfaces.push(this.artSurface);
      const handle=this.addMesh(new THREE.TorusGeometry(.34,.075,18,52),this.material(0xe9ebef,{roughness:.28}),.69,.02,-.015); handle.scale.set(.86,1.12,1);
      const inner=this.addMesh(new THREE.TorusGeometry(.225,.025,12,48),this.material(0xc4c9d1),.70,.02,-.02); inner.scale.set(.86,1.12,1);
    } else if (slug === 'pillow') {
      const shape=roundedRect(1.75,1.48,.27), weave=this.fabricTexture(); const pillow=this.addMesh(this.extruded(shape,.36,.105),this.material(0xe8eaf0,{map:weave,bumpMap:weave,bumpScale:.022,roughness:1}));
      pillow.position.z=0; this.printPlane(this.group,0,0,.31,1.18,1.02,false,'front');
      const stitchMat=new THREE.LineDashedMaterial({color:0x969eaa,dashSize:.035,gapSize:.028,transparent:true,opacity:.68});
      const pts=[]; for(let i=0;i<=50;i++){const a=i/50*Math.PI*2;pts.push(new THREE.Vector3(.78*Math.cos(a),.64*Math.sin(a),.185));}
      const stitch=new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),stitchMat); stitch.computeLineDistances(); this.group.add(stitch);
      for(const side of [-1,1]) {
        const crease=new THREE.CatmullRomCurve3([[side*.64,.43,.19],[side*.52,.2,.24],[side*.61,-.06,.25],[side*.48,-.36,.19]].map(point=>new THREE.Vector3(...point)));
        this.addMesh(new THREE.TubeGeometry(crease,28,.014,7,false),new THREE.MeshStandardMaterial({color:0xc4cad4,transparent:true,opacity:.35,roughness:1}));
      }
    } else if (slug === 'banner') {
      const flex=this.detailTexture('flex');
      const baseMat=this.material(0xe9e7df,{map:flex,bumpMap:flex,bumpScale:.008,roughness:.9}); const banner=this.curvedPlane(this.group,2.6,1.35,baseMat,48,.055); banner.position.y=.05;
      const overlay=this.curvedPlane(this.group,2.59,1.34,new THREE.MeshStandardMaterial({map:this.textureFor('full_front'),transparent:true,alphaTest:.005,roughness:.88,side:THREE.DoubleSide,depthWrite:false}),48,.064); overlay.position.y=.05; overlay.renderOrder=4; overlay.userData.printSurface=true; overlay.userData.printRegion='full_front'; this.artSurface=overlay;this.artSurfaces.push(overlay);
      const rodMat=this.material(0x9299a4,{metalness:.55,roughness:.35});
      this.addMesh(new THREE.CylinderGeometry(.035,.035,2.75,20),rodMat,0,.76,0).rotation.z=Math.PI/2;
      this.addMesh(new THREE.CylinderGeometry(.035,.035,2.75,20),rodMat,0,-.66,0).rotation.z=Math.PI/2;
      for(const x of [-1.22,1.22]) for(const y of [-.55,.62]) { const eye=this.addMesh(new THREE.TorusGeometry(.044,.012,8,20),this.material(0x66717d,{metalness:.8}),x,y,.035); eye.scale.y=.85; }
    } else if (slug === 'frame') {
      const woodGrain=this.detailTexture('wood');
      const wood=new THREE.MeshPhysicalMaterial({color:0x9d744a,map:woodGrain,bumpMap:woodGrain,bumpScale:.014,roughness:.46,metalness:.035,clearcoat:.16,clearcoatRoughness:.42});
      this.addMesh(new THREE.BoxGeometry(1.68,.14,.18),wood,0,1.03,0); this.addMesh(new THREE.BoxGeometry(1.68,.14,.18),wood,0,-1.03,0);
      this.addMesh(new THREE.BoxGeometry(.14,1.94,.18),wood,-.77,0,0); this.addMesh(new THREE.BoxGeometry(.14,1.94,.18),wood,.77,0,0);
      this.addMesh(new THREE.PlaneGeometry(1.4,1.8),this.material(0xf4f0e9),0,0,-.055);
      this.printPlane(this.group,0,0,.065,1.36,1.75,false,'photo');
      const glass=this.addMesh(new THREE.PlaneGeometry(1.36,1.75),new THREE.MeshPhysicalMaterial({color:0xffffff,transparent:true,opacity:.12,roughness:.08,metalness:.05,clearcoat:1,side:THREE.DoubleSide,depthWrite:false}),0,0,.078);
      glass.renderOrder=5;
      const inner=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(1.45,1.84,.015)),new THREE.LineBasicMaterial({color:0xe6c894})); inner.position.z=.055; this.group.add(inner);
    } else if (slug === 'phonecase') {
      const shape=roundedRect(1.12,2.16,.19), cutout=new THREE.Path();
      const x0=-.48,y0=.46,x1=-.08,y1=.98,r=.09;
      cutout.moveTo(x0+r,y0);cutout.lineTo(x0,y0+r);cutout.lineTo(x0,y1-r);cutout.quadraticCurveTo(x0,y1,x0+r,y1);cutout.lineTo(x1-r,y1);cutout.quadraticCurveTo(x1,y1,x1,y1-r);cutout.lineTo(x1,y0+r);cutout.quadraticCurveTo(x1,y0,x1-r,y0);cutout.lineTo(x0+r,y0);
      shape.holes.push(cutout);
      const caseMat=new THREE.MeshPhysicalMaterial({color:shirt,roughness:.36,metalness:.035,clearcoat:.35,clearcoatRoughness:.3});
      this.addMesh(this.extruded(shape,.16,.065),caseMat);
      this.printPlane(this.group,0,-.215,.083,.91,1.25,false,'back');
      const cameraMat=this.material(0x202632,{roughness:.22,metalness:.28});
      this.addMesh(this.extruded(roundedRect(.45,.56,.11),.04,.025),cameraMat,-.28,.72,.085);
      const glass=this.addMesh(this.extruded(roundedRect(.35,.45,.075),.012,.008),this.material(0x090e18,{roughness:.14,metalness:.45}),-.28,.72,.112);
      for(const [x,y] of [[-.36,.82],[-.20,.82],[-.36,.65]]) { const lens=this.addMesh(new THREE.CylinderGeometry(.055,.055,.018,32),new THREE.MeshPhysicalMaterial({color:0x101b2c,metalness:.5,roughness:.12,clearcoat:1,clearcoatRoughness:.08}),x,y,.13); lens.rotation.x=Math.PI/2; }
      glass.castShadow=true;
    } else if (slug === 'poster') {
      const paperGrain=this.detailTexture('paper');
      const paper=this.curvedPlane(this.group,1.72,2.25,this.material(0xf9f7f1,{map:paperGrain,bumpMap:paperGrain,bumpScale:.004,roughness:.98,side:THREE.DoubleSide}),40,.085);
      const posterTexture=this.curvedPlane(this.group,1.7,2.22,new THREE.MeshStandardMaterial({map:this.textureFor('full_front'),transparent:true,alphaTest:.005,roughness:.96,side:THREE.DoubleSide,depthWrite:false}),40,.096); posterTexture.renderOrder=4; posterTexture.userData.printSurface=true; posterTexture.userData.printRegion='full_front'; this.artSurface=posterTexture;this.artSurfaces.push(posterTexture);
      const bottom=this.addMesh(new THREE.CylinderGeometry(.018,.018,1.76,16),this.material(0xd0d1d2),0,-1.12,.02); bottom.rotation.z=Math.PI/2;
    } else if (slug === 'gift') {
      const cardboard=this.detailTexture('cardboard');
      this.addMesh(new THREE.BoxGeometry(1.35,1.15,1.05),this.material(0xd8b77d,{map:cardboard,bumpMap:cardboard,bumpScale:.006,roughness:.68}));
      this.addMesh(new THREE.BoxGeometry(1.4,.16,1.1),this.material(0xc5a16a,{map:cardboard,bumpMap:cardboard,bumpScale:.006,roughness:.64}),0,.61,0);
      this.addMesh(new THREE.BoxGeometry(.16,1.18,1.08),this.material(0xb6405d),0,0,.01);
      this.addMesh(new THREE.BoxGeometry(1.36,.15,.12),this.material(0xb6405d),0,0,.56);
      this.printPlane(this.group,0,0,.535,.72,.68,false,'front');
      for(const x of [-.16,.16]) { const bow=this.addMesh(new THREE.TorusGeometry(.17,.045,12,32),this.material(0xe37b94),x,.79,.05); bow.scale.set(1.2,.72,1); }
    }
    const floorHeights={tshirt:-1.36,mug:-.7,pillow:-.83,banner:-.75,frame:-1.18,phonecase:-1.16,poster:-1.22,gift:-.66};
    this.shadow.position.y=floorHeights[slug]||-1;
    this.group.rotation.set(0,0,0); this.setView(this.angle,this.zoom,this.back); this.updateArtwork(this.artworks||[],this.areaRect||{width:600,height:500});
    this.loadConfiguredAsset(slug);
  }
  setProduct(slug,color=this.color,assetConfig=this.assetConfig) {
    const changed=slug!==this.slug||color!==this.color; this.assetConfig=assetConfig;
    if(!changed) { this.updateArtwork(this.artworks||[],this.areaRect||{width:600,height:500}); return; }
    this.buildProduct(slug,color); this.loadConfiguredAsset(slug,assetConfig);
  }
  setColor(color) {
    this.color=color;
    if(this.slug==='tshirt'||this.slug==='phonecase'||this.slug==='gift') { this.buildProduct(this.slug,color); this.loadConfiguredAsset(this.slug,this.assetConfig); }
    else { const mat=this.group.children.find(child=>child.isMesh)?.material; if(mat?.color) mat.color.setHex(colors[color]||colors.white); }
  }
  updateArtwork(artworks,rect) {
    this.artworks=artworks; this.areaRect=rect;
    for(const [location,canvas] of this.regionCanvases) {
      const ctx=canvas.getContext('2d'); ctx.clearRect(0,0,canvas.width,canvas.height);
      const W=canvas.width,H=canvas.height;
      for(const item of artworks.filter(entry=>(entry.location||Object.keys(productLocations[this.slug]||{})[0])===location)) {
        let image=item.previewImage;
        if(!image) { image=new Image(); image.src=item.url; item.previewImage=image; image.onload=()=>this.updateArtwork(this.artworks,this.areaRect); }
        if(!image.complete||!image.naturalWidth) continue;
        const fit=Math.min(W*.82/image.naturalWidth,H*.82/image.naturalHeight)*item.scale;
        const w=image.naturalWidth*fit,h=image.naturalHeight*fit;
        const offsetX=Number.isFinite(item.previewX)?item.previewX:item.x/(rect.width||W),offsetY=Number.isFinite(item.previewY)?item.previewY:item.y/(rect.height||H);
        ctx.save(); ctx.translate(W/2+offsetX*W,H/2+offsetY*H); ctx.rotate(item.angle*Math.PI/180);
        ctx.drawImage(image,-w/2,-h/2,w,h); ctx.restore();
      }
      this.regionTextures.get(location).needsUpdate=true;
    }
  }
  async loadConfiguredAsset(slug, configured=this.assetConfig) {
    const fallback=reason=>window.dispatchEvent(new CustomEvent('ansuman-preview-quality',{detail:{slug,quality:'stylized',reason}}));
    if(this.assetLoadingSlug===slug) return;
    this.assetLoadingSlug=slug;
    try {
      const entry=configured;
      if(!entry?.model||!entry.license) { fallback('No licensed photorealistic 3D asset is installed for this product.'); return; }
      if(typeof entry.model!=='string'||entry.model.includes('..')||!entry.model.toLowerCase().endsWith('.glb')) throw new Error('The configured product asset path is invalid.');
      const modelUrl=new URL(`./models/${entry.model}`,import.meta.url);
      if(modelUrl.origin!==location.origin||!modelUrl.pathname.includes('/static/models/')) throw new Error('Product models must be stored locally under static/models.');
      const gltf=await this.gltfLoader.loadAsync(modelUrl.href);
      if(this.slug!==slug) return;
      const surfaceNames=entry.print_surfaces||{}, targets=new Map(), ownedTextures=new Set();
      gltf.scene.traverse(node=>{
        if(!node.isMesh) return;
        const isPrintMesh=Object.values(surfaceNames).includes(node.name);
        const materials=Array.isArray(node.material)?node.material:[node.material];
        if(!isPrintMesh&&/(fabric|shirt|cloth|cotton|tee)/i.test(materials.map(material=>material.name||'').join(' '))) {
          const tint=colors[this.color]||colors.white;
          node.material=Array.isArray(node.material)?node.material.map(material=>{const output=material.clone();output.color?.setHex(tint);return output;}):node.material.clone();
          if(!Array.isArray(node.material)) node.material.color?.setHex(tint);
        }
        materials.forEach(material=>{for(const key of ['map','normalMap','roughnessMap','metalnessMap','aoMap','emissiveMap','bumpMap','alphaMap']) if(material[key]?.isTexture) ownedTextures.add(material[key]);});
        const region=Object.keys(surfaceNames).find(key=>surfaceNames[key]===node.name);
        if(region) targets.set(region,node);
      });
      if(Object.keys(surfaceNames).some(region=>!targets.has(region))) throw new Error('The GLB is missing one or more named UV print surfaces.');
      Object.entries(surfaceNames).forEach(([region])=>{
        const mesh=targets.get(region); mesh.userData.printRegion=region; mesh.userData.printSurface=true;
        const apply=material=>{const output=material.clone();output.map=this.textureFor(region);output.color.set(0xffffff);output.transparent=true;output.alphaTest=.005;output.depthWrite=false;output.needsUpdate=true;return output;};
        mesh.material=Array.isArray(mesh.material)?mesh.material.map(apply):apply(mesh.material);
      });
      this.clearGroup(); this.assetOwnedTextures=ownedTextures; this.group.add(gltf.scene);
      this.assetSurfaceMeshes=targets; this.artSurfaces=[...targets.values()]; this.artSurface=this.artSurfaces[0]||null; this.assetMode=true;
      if(Number.isFinite(entry.scale)&&entry.scale>0) gltf.scene.scale.multiplyScalar(entry.scale);
      if(Array.isArray(entry.position)&&entry.position.length===3) gltf.scene.position.set(...entry.position);
      this.updateArtwork(this.artworks||[],this.areaRect||{width:600,height:500});
      window.dispatchEvent(new CustomEvent('ansuman-preview-quality',{detail:{slug,quality:'licensed-model',reason:'Installed licensed UV-mapped product asset.'}}));
    } catch(error) {
      console.warn(`Photorealistic product asset unavailable for ${slug}; the stylized model remains active.`,error);
      fallback(error.message||'The product model could not be loaded.');
    } finally { if(this.assetLoadingSlug===slug) this.assetLoadingSlug=null; }
  }
  setView(angle,zoom,back=false) { this.angle=angle;this.zoom=zoom;this.back=back;this.targetRotation=angle*Math.PI/180+(back?Math.PI:0);this.targetScale=zoom; }
  pointerUv(event) {
    const rect=this.renderer.domElement.getBoundingClientRect(); if(!rect.width||!rect.height||!this.artSurface)return null;
    this.pointer.set(((event.clientX-rect.left)/rect.width)*2-1,-((event.clientY-rect.top)/rect.height)*2+1);
    this.raycaster.setFromCamera(this.pointer,this.camera);
    const hit=this.raycaster.intersectObjects(this.artSurfaces,true)[0]; return hit?.uv?{uv:hit.uv,region:hit.object.userData.printRegion}:null;
  }
  beginArtworkDrag(event,item,rect) {
    const hit=this.pointerUv(event); if(!hit||!item||hit.region!==item.location)return false; const uv=hit.uv;
    const pointerX=(uv.x-.5)*rect.width, pointerY=(.5-uv.y)*rect.height;
    this.artDrag={id:item.id,region:hit.region,offsetX:item.x-pointerX,offsetY:item.y-pointerY}; return true;
  }
  moveArtworkDrag(event,rect) {
    if(!this.artDrag)return null; const hit=this.pointerUv(event); if(!hit||hit.region!==this.artDrag.region)return null; const uv=hit.uv;
    return {x:Math.max(-rect.width*.35,Math.min(rect.width*.35,(uv.x-.5)*rect.width+this.artDrag.offsetX)),
      y:Math.max(-rect.height*.35,Math.min(rect.height*.35,(.5-uv.y)*rect.height+this.artDrag.offsetY))};
  }
  endArtworkDrag() { this.artDrag=null; }
  animate() { this.frame=requestAnimationFrame(()=>this.animate()); this.group.rotation.y+=(this.targetRotation-this.group.rotation.y)*.18; const scale=this.group.scale.x+(this.targetScale-this.group.scale.x)*.18; this.group.scale.setScalar(scale); this.renderer.render(this.scene,this.camera); }
  dispose() { cancelAnimationFrame(this.frame); this.resizeObserver.disconnect(); this.renderer.dispose(); }
}

window.AnsumanProductPreview=ProductPreview;
window.dispatchEvent(new Event('ansuman-three-ready'));
