/* Restaurant POS Branding Studio — shared browser UI, persisted per branch on server. */
'use strict';
const BRAND_THEMES = [
 {id:'classic',title:'Signature',desc:'Warm coral · Classic POS',bg:'#f7f7f4',panel:'#ffffff',ink:'#181d1c',accent:'#ec704c'},
 {id:'midnight',title:'Midnight',desc:'Onyx · Electric violet',bg:'#0d1118',panel:'#171f2b',ink:'#f2f5fa',accent:'#a78bfa'},
 {id:'ivory',title:'Ivory',desc:'Cream · Espresso',bg:'#fbf8f0',panel:'#ffffff',ink:'#282521',accent:'#b66a39'},
 {id:'ocean',title:'Ocean',desc:'Slate blue · Sapphire',bg:'#eef4fa',panel:'#ffffff',ink:'#142b3c',accent:'#2079b4'},
 {id:'forest',title:'Forest',desc:'Sage · Deep green',bg:'#f0f5f0',panel:'#ffffff',ink:'#1b3229',accent:'#307755'},
 {id:'saffron',title:'Saffron',desc:'Warm ivory · Golden',bg:'#fff9eb',panel:'#ffffff',ink:'#33281c',accent:'#c18a20'}
];
function brandName(){return S.branding?.display_name||S.settings.restaurant||'Restaurant'}
function brandLogoMarkup(extraClass=''){
 const url=S.branding?.logo_url;
 return url?`<img class="brand-uploaded-logo ${extraClass}" src="${esc(url)}" alt="${esc(brandName())} logo">`:`<div class="brand-emblem ${extraClass}" aria-hidden="true">${esc((brandName().trim()[0]||'R').toUpperCase())}</div>`;
}
function brandIdentity(){return `<div class="brand">${brandLogoMarkup()}<div class="brand-details"><strong>${esc(brandName())}</strong><span>${esc(S.branding?.tagline||'RESTAURANT POS')}</span></div></div>`}
function brandHeaderMark(){return S.branding?.logo_url?`<img class="brand-top-logo" src="${esc(S.branding.logo_url)}" alt="">`:ic('tables',17)}
function brandPrintHeader(settings=S.settings){const name=S.branding?.display_name||settings.restaurant||'Restaurant';return `${S.branding?.logo_url?`<img class="print-brand-logo" src="${esc(S.branding.logo_url)}" alt="${esc(name)} logo">`:''}<h2>${esc(name)}</h2>`}
function applyBranding(theme){document.body.dataset.theme=BRAND_THEMES.some(t=>t.id===theme)?theme:'classic'}
async function loadBranding(){
 try{S.branding=await api('branding');applyBranding(S.branding.theme)}
 catch(err){console.warn('Branding could not load:',err.message);S.branding={theme:'classic'};applyBranding('classic')}
}
function brandingSettingsPanel(){
 const active=S.branding?.theme||'classic';
 return `<section class="branding-studio" aria-labelledby="branding-head">
 <div class="branding-intro"><div><div class="eyebrow">YOUR RESTAURANT, YOUR IDENTITY</div><h2 id="branding-head">Branding studio</h2><p>Upload a logo and choose a visual style. Each branch can have its own look. Changes also appear on the guest ordering page and printed receipts.</p></div><span class="branding-label">6 curated themes</span></div>
 <div class="branding-layout">
  <div class="branding-controls">
   <section class="brand-setting-card"><h3>01 / Restaurant logo</h3><p class="brand-help">PNG, JPG or WebP · maximum 2 MB · transparencies supported</p>
     <div class="brand-upload-row"><div class="brand-logo-frame">${S.branding?.logo_url?`<img src="${esc(S.branding.logo_url)}" alt="Current restaurant logo">`:`<span class="brand-placeholder">${esc((brandName()[0]||'R').toUpperCase())}</span>`}</div><div class="brand-upload-actions"><label class="btn primary brand-upload-button" for="brand-logo-file">${ic('plus',16)} ${S.branding?.logo_url?'Replace logo':'Upload logo'}</label><input id="brand-logo-file" type="file" accept="image/png,image/jpeg,image/webp" aria-label="Upload restaurant logo">${S.branding?.logo_url?`<button type="button" class="btn small" data-brand-action="remove-logo">Remove logo</button>`:''}<small>Recommended: square artwork, at least 400 × 400 px.</small></div></div>
   </section>
   <section class="brand-setting-card"><h3>02 / Colour theme</h3><p class="brand-help">Select a style to preview it, then save your choice.</p><form id="branding-form" data-branding-form>
   <div class="brand-themes">${BRAND_THEMES.map(t=>`<label class="brand-theme-option"><input type="radio" name="theme" value="${t.id}" ${active===t.id?'checked':''}><span class="theme-swatch" style="background:${t.bg}"><span class="swatch-panel" style="background:${t.panel}"><i style="background:${t.accent}"></i><b style="background:${t.ink}"></b><em style="background:${t.ink}"></em></span></span><span class="theme-info"><strong>${esc(t.title)}</strong><small>${esc(t.desc)}</small></span><span class="theme-check">✓</span></label>`).join('')}</div>
   <div class="brand-detail-fields"><div class="field"><label for="brand-name">Display name (optional)</label><input id="brand-name" name="display_name" maxlength="120" value="${esc(S.branding?.display_name||'')}" placeholder="Default: ${esc(S.settings.restaurant||'Restaurant')}"></div><div class="field"><label for="brand-tagline">Short tagline (optional)</label><input id="brand-tagline" name="tagline" maxlength="100" value="${esc(S.branding?.tagline||'')}" placeholder="Fresh food. Great company."></div></div>
   <button type="submit" class="btn primary">${ic('check',16)} Save branding & theme</button><span id="branding-save-status" class="brand-save-status" role="status" aria-live="polite"></span></form>
   </section>
  </div>
  <aside class="brand-preview-column"><div class="brand-preview-heading"><div class="eyebrow">LIVE PREVIEW</div><h3>See how it looks</h3><p>The whole interface switches as you select a theme.</p></div>
   <div class="brand-preview-card"><div class="brand-preview-bar"><span class="brand-mini-logo">${S.branding?.logo_url?`<img src="${esc(S.branding.logo_url)}" alt="">`:esc((brandName()[0]||'R').toUpperCase())}</span><span class="brand-mini-title">${esc(brandName())}</span><span class="brand-mini-dots">•••</span></div><div class="brand-preview-inner"><small>DAILY OVERVIEW</small><strong>Good food. Good business.</strong><div class="brand-preview-stat-row"><div><small>Today's sales</small><b>${fmt(248750)}</b></div><div><small>Open orders</small><b>12</b></div></div><div class="brand-preview-button">+ New order</div></div></div>
   <div class="brand-preview-note">${ic('check',16)} Preview is temporary until you click Save. Your existing orders, inventory and reports remain unchanged.</div>
  </aside>
 </div></section>`;
}
async function brandRefreshUI(){await loadBranding();shell();if(S.view!=='settings')navigate('settings')}
document.addEventListener('change',async event=>{
 const el=event.target;
 if(el.closest('#branding-form')&&el.name==='theme'){
  applyBranding(el.value);
  const label=BRAND_THEMES.find(t=>t.id===el.value)?.title||'Signature';
  const status=document.querySelector('#branding-save-status');if(status)status.textContent=`Previewing ${label} — not saved yet`;
 }
 if(el.id==='brand-logo-file'&&el.files?.[0]){
  const file=el.files[0];
  if(file.size>2*1024*1024)return toast('Logo must be 2 MB or smaller',true);
  if(!['image/png','image/jpeg','image/webp'].includes(file.type))return toast('Choose PNG, JPG or WebP',true);
  const button=document.querySelector('.brand-upload-button');if(button)button.style.opacity='.5';
  try{
   const img=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('Cannot read this file'));reader.readAsDataURL(file)});
   const raw=String(img).split(',')[1];
   const result=await api('branding/logo',{method:'POST',data:{image_base64:raw,mime_type:file.type}});
   S.branding=result.branding;await brandRefreshUI();toast('Restaurant logo updated');
  }catch(err){toast(err.message,true);if(button)button.style.opacity='1'}
 }
});
document.addEventListener('submit',async event=>{
 const form=event.target.closest('[data-branding-form]');if(!form)return;
 event.preventDefault();
 const btn=form.querySelector('button[type=submit]');btn.disabled=true;
 const vals=Object.fromEntries(new FormData(form));
 try{
  const r=await api('branding',{method:'POST',data:{theme:vals.theme,display_name:vals.display_name||'',tagline:vals.tagline||''}});
  S.branding=r.branding;applyBranding(S.branding.theme);shell();toast('Branding saved successfully');
 }catch(err){toast(err.message,true)}finally{btn.disabled=false}
});
document.addEventListener('click',async event=>{
 if(!event.target.closest('[data-brand-action="remove-logo"]'))return;
 if(!confirm('Remove this branch’s restaurant logo?'))return;
 try{const r=await api('branding/logo',{method:'DELETE'});S.branding=r.branding;await brandRefreshUI();toast('Restaurant logo removed')}
 catch(err){toast(err.message,true)}
});
