'use strict';
const guestRoot=document.getElementById('guest-root');
const guestEsc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const p=new URLSearchParams(location.search),token=p.get('t')||p.get('store')||'';
const isTable=!!p.get('t');
let catalog=null,cart={},busy=false;
const sid=()=>{const name=(catalog?.currency||'PKR');return `${name} ${(Number(0)||0).toFixed(2)}`};
const money=x=>`${guestEsc(catalog?.currency||'PKR')} ${(x/100).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}`;
const icon=m=>/pizza/i.test(m.name)?'🍕':/burger/i.test(m.name)?'🍔':/coffee/i.test(m.name)?'☕':/chicken/i.test(m.name)?'🍗':/fries/i.test(m.name)?'🍟':/salad/i.test(m.name)?'🥗':'🍽️';
const qty=()=>Object.values(cart).reduce((a,b)=>a+b,0);
const total=()=>catalog.menu.reduce((a,i)=>a+(cart[i.id]||0)*i.price_cents,0);
const orderKey=()=>{const k='guest-cart-order-key:'+token;let x=sessionStorage.getItem(k);if(!x){x=(crypto.randomUUID?crypto.randomUUID():`${Date.now()}-${Math.random()}`);sessionStorage.setItem(k,x)}return x};
async function api(path,body){const r=await fetch(path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});const x=await r.json();if(!r.ok)throw Error(x.error||'Unable to continue');return x}
function render(){
 const groups=[...new Set(catalog.menu.map(x=>x.category))];
 guestRoot.innerHTML=`<section class="hero"><div class="kicker">${isTable?'SCAN · SELECT · ORDER':'ORDER ONLINE · PAY LATER'}</div><h1>${guestEsc(isTable?'Your table. Your menu.':'Good food, on your terms.')}</h1><p>${guestEsc(catalog.restaurant)} · ${guestEsc(catalog.branch)}. Browse the menu and send your order directly to the restaurant. Payment is collected by the restaurant.</p></section><div class="guest-grid"><section class="menu-list">${groups.map(cat=>`<div style="margin-top:15px"><div class="kicker" style="margin-bottom:12px">${guestEsc(cat.toUpperCase())}</div>${catalog.menu.filter(m=>m.category===cat).map(m=>`<div class="meal"><div class="meal-art">${icon(m)}</div><div class="meal-text"><strong>${guestEsc(m.name)}</strong><p>${money(m.price_cents)}</p>${m.track_stock&&m.stock<=0?'<small>Sold out</small>':''}</div><button aria-label="Add ${guestEsc(m.name)}" data-add="${m.id}" ${m.track_stock&&m.stock<=0?'disabled':''}>+</button></div>`).join('')}</div>`).join('')}</section>
 <aside class="cart"><h2>Your order <small style="font-size:13px;color:#79867d">(${qty()})</small></h2>${qty()?catalog.menu.filter(m=>cart[m.id]).map(m=>`<div class="cart-line"><span><button data-sub="${m.id}">−</button> ${cart[m.id]} × ${guestEsc(m.name)}</span><b>${money(cart[m.id]*m.price_cents)}</b></div>`).join(''):'<div class="cart-empty">Choose your favourites from the menu to begin.</div>'}<div class="total"><span>Subtotal</span><strong>${money(total())}</strong></div><p style="font-size:11px;color:#79867d">Final tax is calculated when your order is submitted.</p><form id="guest-order"><div class="field"><label>Full name</label><input name="customer_name" required maxlength="100" placeholder="Your name"></div>${!isTable?`<div class="field"><label>Order type</label><select name="service_type" id="guest-type"><option value="takeaway">Takeaway / pickup</option><option value="delivery">Delivery</option></select></div>`:''}<div class="field"><label>Phone number ${isTable?'(optional)':'(recommended)'}</label><input name="phone" maxlength="40" placeholder="03XX-XXXXXXX"></div>${!isTable?`<div class="field" id="delivery-field" style="display:none"><label>Delivery address</label><input name="address" maxlength="300" placeholder="Street, area, landmark"></div>`:''}<div class="field"><label>Any special instructions?</label><textarea name="note" maxlength="450" placeholder="e.g. less spicy"></textarea></div><div class="notice">No online card payment. The restaurant will confirm your order and collect payment separately.</div><button class="primary" style="margin-top:15px" ${!qty()?'disabled':''} type="submit">Place order · ${money(total())}</button></form></aside></div>`;
 document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>{let id=Number(b.dataset.add),m=catalog.menu.find(x=>x.id===id);if((cart[id]||0)>=30||(m.track_stock&&cart[id]>=m.stock))return;cart[id]=(cart[id]||0)+1;render()}));
 document.querySelectorAll('[data-sub]').forEach(b=>b.addEventListener('click',()=>{let id=Number(b.dataset.sub);cart[id]=Math.max(0,(cart[id]||0)-1);render()}));
 document.getElementById('guest-type')?.addEventListener('change',e=>document.getElementById('delivery-field').style.display=e.target.value==='delivery'?'':'none');
 document.getElementById('guest-order').addEventListener('submit',async e=>{
   e.preventDefault();if(busy)return;busy=true;
   const form=e.currentTarget,vals=Object.fromEntries(new FormData(form));
   const items=Object.entries(cart).filter(([_,q])=>q>0).map(([id,q])=>({item_id:Number(id),quantity:q}));
   if(!items.length){busy=false;return}
   const button=form.querySelector('button[type=submit]');button.disabled=true;
   try{const r=await api('/api/public/order',{token,service_type:isTable?'dine_in':vals.service_type,items,
       customer_name:vals.customer_name,phone:vals.phone||'',address:vals.address||'',note:vals.note||'',client_key:orderKey()});
       sessionStorage.removeItem('guest-cart-order-key:'+token);cart={};
       guestRoot.innerHTML=`<section class="confirmation"><div class="kicker">ORDER RECEIVED</div><h2>You're all set.</h2><p>Your order <strong>${guestEsc(r.order_number)}</strong> has reached the restaurant. Keep this number for your reference. Payment is due when the restaurant confirms the order.</p><button class="primary" style="max-width:200px" onclick="location.reload()">New order</button></section>`;
   }catch(err){alert(err.message);button.disabled=false}finally{busy=false}
 });
}
(async()=>{try{catalog=await api('/api/public/catalog?'+(isTable?'t=':'store=')+encodeURIComponent(token));document.body.dataset.theme=catalog.branding?.theme||'classic';const b=catalog.branding||{};const name=b.display_name||catalog.restaurant;const brand=document.querySelector('.guest-logo');if(brand){brand.innerHTML=`${b.logo_url?`<img src="${guestEsc(b.logo_url)}" alt="${guestEsc(name)} logo">`:'<span>◼</span>'}<span><strong>${guestEsc(name)}</strong><small>${guestEsc(b.tagline||'ORDER ONLINE')}</small></span>`}document.title=`Order online | ${name}`;render()}catch(err){guestRoot.innerHTML=`<div class="confirmation"><h2>Ordering unavailable</h2><p>${guestEsc(err.message)}</p></div>`}})();
