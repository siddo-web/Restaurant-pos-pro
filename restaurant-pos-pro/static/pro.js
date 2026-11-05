/* Service POS Pro — modular phase 1/2/3 frontend. Uses the original app's state/API. */
'use strict';
const pList = v => Array.isArray(v) ? v : [];
const proMoney = v => fmt(Number(v)||0);
const proQty = milli => (Number(milli||0)/1000).toLocaleString('en-US',{maximumFractionDigits:3});
const proMilli = v => Math.round(Number(v)*1000);
const proEmpty = msg => `<div class="empty">${esc(msg)}</div>`;
const proField=(label,name,type='text',value='',props='')=>`<div class="field"><label>${esc(label)}</label><input name="${esc(name)}" type="${type}" value="${esc(value)}" ${props}></div>`;
const proPick=(name,rows,display='name',id='id')=>`<select name="${name}" required>${rows.map(x=>`<option value="${x[id]}">${esc(x[display])}</option>`).join('')}</select>`;
const proHeading=(head,desc,action='')=> title('OPERATIONS',head,desc,action);
const P=()=>S.pro||{};
const proDate=t=>t?dateShort(t):'—';
const pnum=n=>Number(n||0).toLocaleString('en-US');
function proSidebar(){
 const admin=canAdmin(),sell=canSell();
 return `<div class="navgroup">OPERATIONS</div>${sell?navItem('shifts','Cashier shifts','clock'):''}${admin?navItem('ingredients','Ingredients & recipes','menu'):''}${admin?navItem('suppliers','Suppliers & purchases','people'):''}${sell?navItem('expenses','Expenses','dollar'):''}${sell?navItem('customers','Customers & loyalty','people'):''}${sell?navItem('deliveries','Delivery board','arrow'):''}${admin?navItem('analytics','Advanced analytics','reports'):''}<div class="navgroup">GROWTH</div>${admin?navItem('qr','QR / online ordering','receipt'):''}${sell?navItem('drafts','Offline drafts','clock'):''}${admin?navItem('branches','Branches','tables'):''}${admin?navItem('audit','Audit trail','check'):''}`;
}
function proBranchHeader(){
 if(!canAdmin())return `<span class="branch-chip">BRANCH ${S.session?.user?.branch_id||1}</span>`;
 const branch=P().branch_id||S.session.user.branch_id||1;
 const a=pList(P().branches);
 return a.length?`<select class="branch-switch" id="pro-branch" aria-label="Active branch">${a.map(x=>`<option value="${x.id}" ${branch===x.id?'selected':''}>${esc(x.name)}</option>`).join('')}</select>`:'';
}
async function loadPro(){
 try{S.pro=await api('pro/bootstrap');if(canSell())S.pro.report=await api('pro/reports')}catch(e){console.warn('Operational modules unavailable:',e.message);S.pro={}}
 if(canAdmin())try{S.pro.qr=await api('pro/tables/qr')}catch(e){}
}
let proEvents=null,proRefreshBusy=false;
function proLiveConnect(){
 if(proEvents){proEvents.close();proEvents=null}
 if(typeof EventSource==='undefined'||!S.session?.authenticated)return;
 proEvents=new EventSource('/api/events');
 proEvents.addEventListener('sync',async()=>{
   if(proRefreshBusy||!S.session?.authenticated||document.hidden)return;
   if(['register','settings'].includes(S.view)||$('#modal-root')?.innerHTML)return;
   proRefreshBusy=true;
   try{await refresh()}catch(e){}finally{proRefreshBusy=false}
 });
 proEvents.onerror=()=>{ /* EventSource handles reconnect. */ };
}
function proViews(){return {shifts:proShifts,ingredients:proIngredients,suppliers:proSuppliers,expenses:proExpenses,
  customers:proCustomers,deliveries:proDeliveries,analytics:proAnalytics,qr:proQr,branches:proBranches,
  audit:proAudit,drafts:proDrafts,tables:proFloor};}
function proCard(title,body,meta=''){return `<section class="card pro-card"><div class="pro-card-top"><h3>${title}</h3>${meta}</div>${body}</section>`}
function proButton(text,action,extras=''){return `<button class="btn primary" data-pro-action="${action}" ${extras}>${ic('plus',15)} ${text}</button>`}
function proTable(headers,rows){return `<div class="card table-scroll"><table class="data-table pro-table"><thead><tr>${headers.map(h=>`<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')||`<tr><td colspan="${headers.length}" class="empty">No records yet.</td></tr>`}</tbody></table></div>`}
function proShifts(){
 const sh=P().current_shift,items=pList(P().shifts);
 return `${proHeading('Cashier shifts','Open your till before taking payments, reconcile cash and record any variance.',sh?`<button class="btn primary" data-pro-action="shift-close">${ic('check')} Close my shift</button>`:proButton('Open my shift','shift-open'))}
 <div class="stats-grid">${stat('My shift',sh?'OPEN':'CLOSED','clock',sh?`Started ${proDate(sh.opened_at)}`:'Open to begin a till session',sh?'green':'')}${stat('Starting cash',proMoney(sh?.opening_cash_cents),'dollar','Float at shift opening')}${stat('Shifts on record',items.length,'reports','Recent branch sessions')}${stat('Last variance',proMoney(items.find(x=>x.closed_at)?.variance_cents),'check','Positive = cash over / negative = short')}</div>
 ${canAdmin()?proTable(['Staff','Opened','Closed','Opening','Expected','Counted','Variance'],items.map(x=>`<tr><td><strong>${esc(x.user_name)}</strong></td><td>${proDate(x.opened_at)}</td><td>${proDate(x.closed_at)}</td><td>${proMoney(x.opening_cash_cents)}</td><td>${proMoney(x.expected_cash_cents)}</td><td>${proMoney(x.closing_cash_cents)}</td><td>${x.closed_at?proMoney(x.variance_cents):'<span class="status new">Open</span>'}</td></tr>`)):proCard('Till reconciliation','<p class="muted">At shift close, the system calculates expected cash from your recorded cash payments, refunds and cash expenses.</p>')}`;
}
function proIngredients(){
 const a=pList(P().ingredients),rec=pList(P().recipes);
 const low=a.filter(x=>x.active&&x.stock_milli<=x.low_stock_milli);
 const stockCost=a.reduce((n,x)=>n+Math.round(x.stock_milli*x.cost_cents/1000),0);
 return `${proHeading('Ingredients & recipes','Ingredient-level tracking, recipe consumption and cost per dish.',proButton('Add ingredient','ingredient-add'))}
 <div class="stats-grid">${stat('Ingredients',a.length,'menu','Across this branch')}${stat('Low-stock alerts',low.length,'clock','At or below reorder level',low.length?'blue':'green')}${stat('Stock valuation',proMoney(stockCost),'dollar','Estimated from last unit cost')}${stat('Recipe mappings',rec.length,'receipt','Ingredient connections')}</div>
 <div class="pro-grid-2">${proCard('Ingredients',`<div class="pro-list">${a.map(x=>`<div class="pro-row"><div class="pro-icon">${ic('menu')}</div><div class="pro-grow"><strong>${esc(x.name)}</strong><small>${proQty(x.stock_milli)} ${esc(x.unit)} · cost ${proMoney(x.cost_cents)} per ${esc(x.unit)}</small></div>${x.stock_milli<=x.low_stock_milli?'<span class="status new">Low stock</span>':''}<button class="btn small" data-pro-action="ingredient-edit" data-id="${x.id}">Edit</button></div>`).join('')||proEmpty('Add an ingredient to get started')}</div>`)}
 ${proCard('Recipe builder',`<p class="muted">Connect the exact quantity of ingredients used by one menu item. Consumption happens when the order is paid.</p><button class="btn primary" data-pro-action="recipe-add">${ic('plus')} Add recipe mapping</button><div class="pro-list pro-scroll">${rec.map(x=>`<div class="pro-row"><div class="pro-grow"><strong>${esc(x.item_name)}</strong><small>${esc(x.ingredient_name)} · ${proQty(x.quantity_milli)} ${esc(x.unit)} per dish</small></div><button class="btn small" data-pro-action="recipe-remove" data-menu="${x.menu_item_id}" data-ing="${x.ingredient_id}">${ic('close',13)}</button></div>`).join('')||proEmpty('No recipes assigned yet')}</div>`)}</div>`;
}
function proSuppliers(){
 let sup=pList(P().suppliers),buy=pList(P().purchases);
 return `${proHeading('Suppliers & purchasing','Receive ingredients into stock with a purchase history.',`<div class="inline-actions">${proButton('Supplier','supplier-add')}${proButton('Receive stock','purchase-add')}</div>`)}
 <div class="pro-grid-2">${proCard('Suppliers',`<div class="pro-list">${sup.map(x=>`<div class="pro-row"><div class="pro-icon">${ic('people')}</div><div class="pro-grow"><strong>${esc(x.name)}</strong><small>${esc(x.phone||x.email||'No contact added')}</small></div></div>`).join('')||proEmpty('No suppliers added')}</div>`)}
 ${proCard('Recent stock receipts',`<div class="pro-list">${buy.map(x=>`<div class="pro-row"><div class="pro-grow"><strong>${esc(x.ingredient)} · ${proQty(x.quantity_milli)}</strong><small>${esc(x.supplier)} · ${proDate(x.created_at)}</small></div><strong>${proMoney(x.amount_cents)}</strong></div>`).join('')||proEmpty('No stock purchases yet')}</div>`)}</div>
 ${proCard('Stock adjustments',`<p class="muted">Record spoiled or wasted inventory with a reason. Stock decreases immediately and the operation is logged.</p><button class="btn" data-pro-action="waste-add">${ic('edit')} Record waste</button>`)}`;
}
function proExpenses(){
 const items=pList(P().expenses);
 return `${proHeading('Daily expenses','Track operating costs and cash drawer deductions.',proButton('Add expense','expense-add'))}
 <div class="stats-grid">${stat('Recorded expenses',items.length,'receipt','Last 100 entries')}${stat('Expense total',proMoney(items.reduce((s,x)=>s+x.amount_cents,0)),'dollar','Listed entries only')}${stat('Cash expenses',proMoney(items.filter(x=>x.method==='cash').reduce((s,x)=>s+x.amount_cents,0)),'dollar','Included in cashier close')}</div>
 ${canAdmin()?proTable(['Category','Description','Payment','Amount','Recorded'],items.map(x=>`<tr><td><span class="category-tag">${esc(x.category)}</span></td><td>${esc(x.description)}</td><td>${esc(x.method)}</td><td><strong>${proMoney(x.amount_cents)}</strong></td><td>${proDate(x.created_at)}</td></tr>`)):proCard('Your expenses','<p class="muted">Managers can review all expenses. Your cash expenses also contribute to till reconciliation.</p>')}`;
}
function proCustomers(){
 const a=pList(P().customers);
 return `${proHeading('Customers & loyalty','Customer history and reward points for completed orders.',proButton('Add customer','customer-add'))}
 <div class="stats-grid">${stat('Customer profiles',a.length,'people','This branch')}${stat('Available points',pnum(a.reduce((s,x)=>s+x.points,0)),'check','Across customer accounts')}${stat('Reward program','1 / 100','receipt','Earn 1 point per 100 whole currency units')}</div>
 ${proTable(['Customer','Contact','Points','Paid orders','Lifetime sales','Actions'],a.map(x=>`<tr><td><strong>${esc(x.name)}</strong></td><td>${esc(x.phone)}<small>${esc(x.email)}</small></td><td><span class="status ready">${x.points} pts</span></td><td>${x.paid_orders||0}</td><td>${proMoney(x.lifetime_spend_cents)}</td><td><button class="btn small" data-pro-action="customer-edit" data-id="${x.id}">${ic('edit',14)} Edit</button></td></tr>`))}
 <div class="alert">Link a customer to a new register order to earn points on payment. Points can be redeemed on open linked orders before checkout.</div>`;
}
function proDeliveries(){
 const a=pList(P().deliveries), pending=a.filter(x=>x.status!=='delivered'&&x.status!=='failed');
 return `${proHeading('Delivery tracking','Manually assign drivers and move delivery orders through dispatch.',proButton('New delivery order','delivery-new'))}
 <div class="stats-grid">${stat('In progress',pending.length,'arrow','Not delivered yet')}${stat('Delivered',a.filter(x=>x.status==='delivered').length,'check','Recent delivery jobs')}${stat('Pending assignment',a.filter(x=>x.status==='pending').length,'clock','Awaiting dispatch')}</div>
 ${proTable(['Order','Customer','Address','Driver','Status','Update'],a.map(x=>`<tr><td><strong>${esc(x.order_number)}</strong><small>${proMoney(x.total_cents)}</small></td><td>${esc(x.customer_name)}</td><td class="pro-address">${esc(x.address)}</td><td>${esc(x.driver||'Unassigned')}</td><td>${status(x.status)}</td><td><button class="btn small" data-pro-action="delivery-update" data-id="${x.order_id}">Update</button></td></tr>`))}`;
}
function proAnalytics(){
 const r=P().report||{orders:0,net_sales_cents:0,refunds_cents:0,cogs_cents:0,expenses_cents:0,estimated_profit_cents:0,top_items:[],tenders:[],trend:[]};
 return `${proHeading('Financial intelligence','Sales, refunds, ingredient costs and operating expenses for your selected date range.',`<button class="btn" data-pro-action="export">${ic('arrow')} Export CSV</button>`)}
 <div class="toolbar pro-filters"><label>From <input id="pro-from" type="date" value="${esc(P().from||r.from_date||'')}"></label><label>To <input id="pro-to" type="date" value="${esc(P().to||r.to_date||'')}"></label><button class="btn primary" data-pro-action="report-apply">Run report</button></div>
 <div class="stats-grid">${stat('Net revenue',proMoney(r.net_sales_cents),'dollar','After recorded refunds')}${stat('Recipe cost',proMoney(r.cogs_cents),'menu','Ingredient costs from paid orders','blue')}${stat('Expenses',proMoney(r.expenses_cents),'reports','Costs entered into POS')}${stat('Est. profit',proMoney(r.estimated_profit_cents),'check','Before unrecorded overhead','green')}</div>
 <div class="pro-grid-2">${proCard('Revenue breakdown',`<div class="pro-kpis">${[['Gross sales',r.gross_sales_cents],['Refunds',r.refunds_cents],['Taxes collected',r.tax_cents],['Discounts',r.discounts_cents],['Total paid orders',null]].map(([k,v])=>`<div><span>${k}</span><strong>${v===null?pnum(r.orders):proMoney(v)}</strong></div>`).join('')}</div>`)}
 ${proCard('Top menu items',pList(r.top_items).map((x,i)=>`<div class="pro-row"><b>${String(i+1).padStart(2,'0')}</b><span class="pro-grow">${esc(x.name)}</span><strong>${pnum(x.quantity)} sold</strong></div>`).join('')||proEmpty('No sales for this period'))}</div>
 ${proCard('Sales by day',`<div class="pro-chart">${pList(r.trend).map(x=>`<div class="pro-bar-row"><span>${esc(x.day)}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.round(100*x.sales_cents/Math.max(1,...r.trend.map(t=>t.sales_cents)))}%"></div></div><strong>${proMoney(x.sales_cents)}</strong></div>`).join('')||proEmpty('Choose a date range with sales')}</div>`)}
 <p class="subtle-note">${esc(r.profit_note||'Cost/profit figures are estimates. All dates are UTC.')}</p>`;
}
function proQr(){
 const links=pList(P().qr?.links),online=P().qr?.online||'';
 return `${proHeading('QR & online ordering','Print table QR codes or share a branch storefront link. Guest orders arrive unpaid in the kitchen.',`<button class="btn" data-pro-action="open-store">${ic('arrow')} Preview storefront</button>`)}
 ${proCard('Public online ordering',`<div class="pro-copy"><code>${esc(location.origin+online)}</code><button class="btn small" data-pro-action="copy-store">${ic('check')} Copy link</button></div><p class="muted">Takeaway and delivery are request-and-pay-later orders. No online gateway is connected.</p>`)}
 <div class="qr-grid">${links.map(x=>`<article class="card qr-card"><img loading="lazy" alt="QR code for ${esc(x.label)}" src="/api/pro/tables/${x.id}/qr.svg"><strong>${esc(x.label)}</strong><small>Table guest ordering</small><button class="btn small" data-pro-action="print-qr" data-id="${x.id}">Print table QR</button></article>`).join('')||proEmpty('Add tables to generate QR codes')}</div>`;
}
function proBranches(){
 const rows=pList(P().branches),current=P().branch_id;
 return `${proHeading('Branch management','Separate order books, table codes, ingredient stock and reporting for each branch.',proButton('Create branch','branch-add'))}
 ${proTable(['Branch','Code','Status','Active'],rows.map(x=>`<tr><td><strong>${esc(x.name)}</strong></td><td><code>${esc(x.code)}</code></td><td>${x.active?'<span class="status ready">Active</span>':'Inactive'}</td><td>${x.id===current?'<strong>Current</strong>':`<button class="btn small" data-pro-action="branch-switch" data-id="${x.id}">Switch</button>`}</td></tr>`))}
 <div class="alert">Creating a branch generates 8 new tables and optionally copies the menu as independent items. Branch-specific pricing and stock are managed after switching.</div>`;
}
function proAudit(){
 return `${proHeading('Activity log','A recorded history of sensitive operational actions for managers.')}
 ${proTable(['Time (UTC)','Actor','Action','Record','Detail'],pList(P().recent_audits).map(x=>`<tr><td>${proDate(x.created_at)}</td><td>${esc(x.actor)}</td><td><span class="category-tag">${esc(x.action)}</span></td><td>${esc(x.entity)} #${esc(x.entity_id)}</td><td class="pro-address">${esc(x.detail||'—')}</td></tr>`))}`;
}
function localDrafts(){try{return JSON.parse(localStorage.getItem('service-drafts-v1')||'[]')}catch{return []}}
function saveDrafts(items){try{localStorage.setItem('service-drafts-v1',JSON.stringify(items))}catch(e){throw Error('This browser cannot store drafts')}}
function proDrafts(){const items=localDrafts();return `${proHeading('Offline drafts','Draft carts can be saved on this device. Sync requires a connection and manual review.',`<button class="btn primary" data-pro-action="draft-register">${ic('plus')} Create draft</button>`)}
 <div class="alert"><strong>Offline limitation:</strong> Saved drafts are not paid orders, do not reserve tables, and are not sent to the kitchen until you manually sync them online. Device storage can be cleared by the browser; do not treat this as a guaranteed offline POS.</div>
 ${proTable(['Saved','Service','Table','Items','Branch','Action'],items.map(x=>`<tr><td>${proDate(x.saved_at)}</td><td>${esc(x.payload.service_type)}</td><td>${esc(S.tables.find(t=>t.id===x.payload.table_id)?.label||'—')}</td><td>${x.payload.items.reduce((n,t)=>n+t.quantity,0)}</td><td>${x.branch_id}</td><td><div class="inline-actions"><button class="btn small" data-pro-action="draft-sync" data-key="${esc(x.key)}">Sync</button><button class="btn small" data-pro-action="draft-delete" data-key="${esc(x.key)}">Delete</button></div></td></tr>`))}`}
function proFloor(){
 const tables=S.tables.filter(t=>t.active),zones=[...new Set(tables.map(t=>t.zone))];
 const busy=tables.filter(t=>occupiedFor(t.id)).length;
 return `${proHeading('Floor & tables',`${busy} occupied · ${tables.length-busy} available · Tap any table to open a ticket.`,canAdmin()?`<button class="btn" data-action="new-table">${ic('plus')} Add table</button>`:'')}
 <div class="pro-floor-stats"><span><i class="dot open"></i> Available</span><span><i class="dot busy"></i> Occupied</span><span><i class="dot prep"></i> Preparing</span><span class="muted">Click an occupied table to transfer it or open its order.</span></div>
 ${zones.map(z=>`<section class="pro-zone"><div class="pro-zone-title"><strong>${esc(z)}</strong><small>${tables.filter(t=>t.zone===z).length} tables</small></div><div class="pro-table-grid">${tables.filter(t=>t.zone===z).map(t=>{const o=occupiedFor(t.id);return `<button class="pro-table-tile ${o?'is-occupied':''} ${o&&o.kitchen_status==='preparing'?'is-preparing':''}" data-action="table-click" data-id="${t.id}"><div class="pro-table-top"><span>${ic('tables',20)}</span><span class="pro-table-led"></span></div><strong>${esc(t.label)}</strong><small>${t.seats} seats</small><div class="pro-table-foot"><span>${o?esc(o.kitchen_status):'Available'}</span><b>${o?proMoney(o.total_cents):'+'}</b></div></button>`}).join('')}</div></section>`).join('')}`;
}
function proOrderButtons(o){return `<button class="btn" data-pro-action="kot" data-id="${o.id}">${ic('print',15)} KOT</button>${canAdmin()&&o.status==='paid'?`<button class="btn" data-pro-action="refund" data-id="${o.id}">Refund</button>`:''}${o.status!=='paid'&&o.status!=='void'?`${o.service_type==='dine_in'?`<button class="btn" data-pro-action="table-transfer" data-id="${o.id}">Move table</button>`:''}${o.customer_id?`<button class="btn" data-pro-action="loyalty-redeem" data-id="${o.id}">Redeem points</button>`:''}`:''}`;}
function proModal(title,form,foot=''){openModal(title,form,foot,true)}
function proForm(type,body,save='Save changes'){return `<form data-pro-form="${type}">${body}<button class="btn primary full" type="submit">${ic('check',16)} ${save}</button></form>`}
function proStockUnits(){return pList(P().ingredients)}
function proOpen(action,id){
 const ing=proStockUnits(),sup=pList(P().suppliers),clients=pList(P().customers);
 if(action==='shift-open')return proModal('Start cashier shift',proForm('shift-open',proField('Opening cash (currency)','cash','number','0','min="0" step="0.01" required'),'Open shift'));
 if(action==='shift-close')return proModal('Close cashier shift',proForm('shift-close',`<p class="muted">Count the physical cash currently in your drawer, including your opening float.</p>${proField('Counted cash','cash','number','0','min="0" step="0.01" required')}${proField('Notes','note','text','','maxlength="300"')}`,'Close and reconcile'));
 if(action==='ingredient-add'||action==='ingredient-edit'){
  const x=ing.find(t=>t.id===id)||{};
  return proModal(x.id?'Edit ingredient':'New ingredient',proForm('ingredient',`<input type="hidden" name="id" value="${x.id||''}"><div class="form-grid">${proField('Ingredient name','name','text',x.name||'','required maxlength="120"')}<div class="field"><label>Unit</label><select name="unit">${['g','ml','pcs'].map(v=>`<option ${x.unit===v?'selected':''}>${v}</option>`).join('')}</select></div>${proField('Current stock in units','stock','number',proQty(x.stock_milli),'min="0" step="0.001" required')}${proField('Low stock warning','threshold','number',proQty(x.low_stock_milli),'min="0" step="0.001" required')}${proField('Cost per unit in currency','cost','number',num(x.cost_cents),'min="0" step="0.01" required')}</div>`))}
 if(action==='recipe-add')return proModal('Build a dish recipe',proForm('recipe',`<div class="field"><label>Menu item</label>${proPick('menu_id',S.menu,'name')}</div><div class="field"><label>Ingredient</label>${proPick('ingredient_id',ing)}</div>${proField('Ingredient quantity for ONE dish','quantity','number','0.100','min="0.001" step="0.001" required')}`,'Link ingredient'));
 if(action==='supplier-add')return proModal('Add supplier',proForm('supplier',proField('Supplier name','name','text','','required')+proField('Phone','phone')+proField('Email','email','email')));
 if(action==='purchase-add')return proModal('Receive ingredient stock',proForm('purchase',`<div class="field"><label>Supplier</label>${proPick('supplier_id',sup)}</div><div class="field"><label>Ingredient</label>${proPick('ingredient_id',ing)}</div>${proField('Quantity received (units)','quantity','number','1','min="0.001" step="0.001" required')}${proField('Total purchase cost','cost','number','0','min="0" step="0.01" required')}${proField('Reference note','note')}`,'Receive stock'));
 if(action==='waste-add')return proModal('Record stock waste',proForm('waste',`<div class="field"><label>Ingredient</label>${proPick('ingredient_id',ing)}</div>${proField('Quantity wasted','quantity','number','1','min="0.001" step="0.001" required')}${proField('Reason','reason','text','','required')}`,'Deduct stock'));
 if(action==='expense-add')return proModal('Record operating expense',proForm('expense',`<div class="form-grid">${proField('Category','category','text','Utilities','required')}${proField('Amount','amount','number','0','min="0.01" step="0.01" required')}</div>${proField('Description','description','text','','required')}<div class="field"><label>Payment method</label><select name="method"><option>cash</option><option>card</option><option>bank</option><option>other</option></select></div>`,'Save expense'));
 if(action==='customer-add'||action==='customer-edit'){
  const c=clients.find(t=>t.id===id)||{};
  return proModal(c.id?'Edit customer':'Add customer',proForm('customer',`<input type="hidden" name="id" value="${c.id||''}">${proField('Name','name','text',c.name||'','required')}${proField('Phone','phone','tel',c.phone||'','required')}${proField('Email (optional)','email','email',c.email||'')}${proField('Address (optional)','address','text',c.address||'')}`))}
 if(action==='branch-add')return proModal('Create new branch',proForm('branch',`${proField('Branch name','name','text','','required')}${proField('Branch code (unique)','code','text','','required pattern="[A-Za-z0-9_-]{2,12}"')}<label class="pro-check"><input type="checkbox" name="clone_menu" checked> Copy current menu to new branch (independent prices)</label>`,'Create branch'));
 if(action==='delivery-update'){
  let d=pList(P().deliveries).find(x=>x.order_id===id)||{};
  return proModal('Update delivery '+esc(d.order_number||id),proForm('delivery',`<input type="hidden" name="id" value="${id}">${proField('Driver','driver','text',d.driver||'')}${proField('Delivery address','address','text',d.address||'')}${proField('Phone number','phone','tel',d.phone||'')}<div class="field"><label>Status</label><select name="status">${['pending','assigned','picked_up','delivered','failed'].map(x=>`<option ${d.status===x?'selected':''} value="${x}">${x.replace('_',' ')}</option>`).join('')}</select></div>`,'Save delivery'))}
 if(action==='refund')return proModal('Record refund',proForm('refund',`<input name="id" type="hidden" value="${id}">${proField('Refund amount','amount','number','','min="0.01" step="0.01" required')}<div class="field"><label>Refund method</label><select name="method"><option>cash</option><option>card</option><option>other</option></select></div>${proField('Reason','reason','text','','required')}<label class="pro-check"><input name="restock" type="checkbox"> Return inventory on final full refund</label><p class="subtle-note">Card refunds here only record a refund made externally. This action does not contact your card provider.</p>`,'Record refund'));
 if(action==='table-transfer')return proModal('Move table order',proForm('transfer',`<input name="id" type="hidden" value="${id}"><div class="field"><label>Move to available table</label><select name="table_id" required>${S.tables.filter(x=>x.active&&!occupiedFor(x.id)).map(x=>`<option value="${x.id}">${esc(x.label)} · ${esc(x.zone)}</option>`).join('')}</select></div>`,'Move order'));
 if(action==='loyalty-redeem'){
  let o=S.orders.find(x=>x.id===id),c=clients.find(x=>x.id===o?.customer_id);
  return proModal('Redeem loyalty points',proForm('redeem',`<input type="hidden" name="id" value="${id}"><p class="muted">${esc(c?.name||'Customer')} has ${c?.points||0} points. 1 point = ${proMoney(100)} discount.</p>${proField('Points to redeem','points','number','1',`min="1" max="${c?.points||1}" step="1" required`)}`,'Apply points'));
 }
}
async function proKot(id){
 const r=await api(`pro/orders/${id}/kot`),o=r.order;
 const markup=`<div class="previewreceipt">${brandPrintHeader()}<div class="center"><strong>KITCHEN / KOT</strong></div><hr><div class="center"><strong>${esc(o.order_number)}</strong></div><div class="line"><span>${esc(o.table_label||o.service_type)}</span><span>${proDate(o.created_at)}</span></div><hr>${o.items.map(x=>`<div class="line"><strong>${x.quantity} × ${esc(x.item_name)}</strong><span></span></div>${x.note?`<small>${esc(x.note)}</small>`:''}`).join('')}<hr><strong>Instructions</strong><p>${esc(o.note||'None')}</p><hr><div class="center">${esc(o.kitchen_status)}</div></div>`;
 $('#print-area').innerHTML=markup;
 openModal('Kitchen ticket',markup+`<div class="modal-foot"><button class="btn primary" data-action="print">${ic('print')} Print KOT</button></div>`);
}
function proSaveDraft(){
 if(!S.cart.length)throw Error('Add items to the cart first');
 if(S.service==='dine_in'&&!S.tableId)throw Error('Select a table first');
 if(S.orderId)throw Error('Existing orders cannot be saved as offline drafts');
 const key=(typeof crypto!=='undefined'&&crypto.randomUUID)?crypto.randomUUID():`${Date.now()}-${Math.random().toString(36).slice(2)}`;
 let payload={service_type:S.service,table_id:S.service==='dine_in'?Number(S.tableId):null,
  customer_name:S.customer,note:S.note,delivery_phone:S.deliveryPhone||'',delivery_address:S.deliveryAddress||'',discount_cents:S.discount,
  customer_id:S.customerId?Number(S.customerId):null,
  client_key:key,items:S.cart.map(x=>({item_id:x.item_id,quantity:x.quantity,note:x.note}))};
 const drafts=localDrafts();drafts.unshift({key,saved_at:new Date().toISOString(),branch_id:P().branch_id,payload});
 saveDrafts(drafts);toast('Draft saved locally; no kitchen ticket created');
}
async function proAction(el,e){
 const action=el.dataset.proAction,id=Number(el.dataset.id);
 try{
  if(['shift-open','shift-close','ingredient-add','ingredient-edit','recipe-add','supplier-add','purchase-add','waste-add','expense-add','customer-add','customer-edit','branch-add','delivery-update','refund','table-transfer','loyalty-redeem'].includes(action))return proOpen(action,id);
  if(action==='recipe-remove'){
    if(confirm('Remove this recipe ingredient?')){await api(`pro/recipes/${Number(el.dataset.menu)}/${Number(el.dataset.ing)}`,{method:'DELETE'});await refresh()};return;
  }
  if(action==='save-draft')return proSaveDraft();
  if(action==='draft-register')return navigate('register');
  if(action==='draft-delete'){saveDrafts(localDrafts().filter(x=>x.key!==el.dataset.key));renderPage();return}
  if(action==='draft-sync'){
    if(!navigator.onLine)throw Error('Connect to the internet/server before syncing');
    const d=localDrafts().find(x=>x.key===el.dataset.key);
    if(!d)return;
    if(d.branch_id!==P().branch_id)throw Error('Switch to the draft’s original branch first');
    if(!confirm('Submit this saved draft as a real unpaid order? Check items and table availability first.'))return;
    const r=await api('orders',{method:'POST',data:d.payload});
    saveDrafts(localDrafts().filter(x=>x.key!==el.dataset.key));toast(`${r.order.order_number} sent to kitchen`);
    await refresh();return;
  }
  if(action==='kot')return proKot(id);
  if(action==='branch-switch'){
    await api('pro/branches/switch',{method:'POST',data:{branch_id:id}});
    closeModal();S.cart=[];S.orderId=null;S.customerId='';await init();toast('Branch changed');return;
  }
  if(action==='report-apply'){
    const a=$('#pro-from').value,b=$('#pro-to').value;
    S.pro.from=a;S.pro.to=b;S.pro.report=await api(`pro/reports?from_date=${encodeURIComponent(a)}&to_date=${encodeURIComponent(b)}`);
    renderPage();return;
  }
  if(action==='export'){
    const a=P().from||P().report?.from_date||'',b=P().to||P().report?.to_date||'';
    location.href=`/api/pro/reports/export?from_date=${encodeURIComponent(a)}&to_date=${encodeURIComponent(b)}`;return;
  }
  if(action==='open-store')return window.open(P().qr?.online||'/guest','_blank','noopener,noreferrer');
  if(action==='copy-store'){
    try{await navigator.clipboard.writeText(location.origin+(P().qr?.online||''));toast('Link copied')}
    catch{toast('Copy the link shown above',true)}return;
  }
  if(action==='print-qr'){
    const x=pList(P().qr?.links).find(x=>x.id===id);
    $('#print-area').innerHTML=`<div class="previewreceipt center">${brandPrintHeader()}<img style="width:230px;height:230px" alt="QR" src="/api/pro/tables/${id}/qr.svg"><h3>Scan to order — ${esc(x?.label)}</h3><p>${esc(location.origin+(x?.url||''))}</p></div>`;
    openModal('Table QR code',`<div class="previewreceipt center"><img width="230" height="230" src="/api/pro/tables/${id}/qr.svg"><h3>${esc(x?.label||'Table')}</h3></div><div class="modal-foot"><button class="btn primary" data-action="print">${ic('print')} Print QR</button></div>`);return;
  }
  if(action==='delivery-new'){S.service='delivery';navigate('register');return}
 }catch(error){toast(error.message,true)}
}
async function proSubmit(form){
 const kind=form.dataset.proForm,v=Object.fromEntries(new FormData(form));
 const post=(route,data)=>api(route,{method:'POST',data});
 if(kind==='shift-open')await post('pro/shifts/open',{opening_cash_cents:toCents(v.cash)});
 if(kind==='shift-close')await post(`pro/shifts/${P().current_shift.id}/close`,{closing_cash_cents:toCents(v.cash),note:v.note});
 if(kind==='ingredient')await post('pro/ingredients',{id:v.id?Number(v.id):null,name:v.name,unit:v.unit,stock_milli:proMilli(v.stock),low_stock_milli:proMilli(v.threshold),cost_cents:toCents(v.cost),active:true});
 if(kind==='recipe')await post(`pro/recipes/${Number(v.menu_id)}`,{ingredient_id:Number(v.ingredient_id),quantity_milli:proMilli(v.quantity)});
 if(kind==='supplier')await post('pro/suppliers',{name:v.name,phone:v.phone,email:v.email});
 if(kind==='purchase')await post('pro/purchases',{supplier_id:Number(v.supplier_id),ingredient_id:Number(v.ingredient_id),quantity_milli:proMilli(v.quantity),amount_cents:toCents(v.cost),note:v.note});
 if(kind==='waste')await post('pro/waste',{ingredient_id:Number(v.ingredient_id),quantity_milli:proMilli(v.quantity),reason:v.reason});
 if(kind==='expense')await post('pro/expenses',{category:v.category,description:v.description,amount_cents:toCents(v.amount),method:v.method});
 if(kind==='customer')await post('pro/customers',{id:v.id?Number(v.id):null,name:v.name,phone:v.phone,email:v.email,address:v.address});
 if(kind==='branch')await post('pro/branches',{name:v.name,code:v.code,clone_menu:!!v.clone_menu});
 if(kind==='delivery')await post(`pro/deliveries/${Number(v.id)}`,{status:v.status,driver:v.driver,address:v.address,phone:v.phone});
 if(kind==='refund')await post(`pro/orders/${Number(v.id)}/refund`,{amount_cents:toCents(v.amount),method:v.method,reason:v.reason,restock:!!v.restock});
 if(kind==='transfer')await post(`pro/orders/${Number(v.id)}/transfer`,{table_id:Number(v.table_id)});
 if(kind==='redeem')await post(`pro/orders/${Number(v.id)}/loyalty/redeem`,{points:Number(v.points)});
 closeModal();await refresh();toast('Changes saved successfully');
}
document.addEventListener('click',e=>{
 const btn=e.target.closest('[data-pro-action]');
 if(btn){e.preventDefault();proAction(btn,e)}
});
document.addEventListener('submit',async e=>{
 const form=e.target.closest('[data-pro-form]');if(!form)return;e.preventDefault();
 const btn=form.querySelector('[type=submit]');if(btn)btn.disabled=true;
 try{await proSubmit(form)}catch(err){toast(err.message,true)}finally{if(btn)btn.disabled=false}
});
document.addEventListener('change',async e=>{
 if(e.target.id==='pro-branch'){
   const value=Number(e.target.value);if(value===P().branch_id)return;
   try{await api('pro/branches/switch',{method:'POST',data:{branch_id:value}});S.cart=[];S.orderId=null;S.customerId='';await init();toast('Branch switched')}
   catch(err){toast(err.message,true);e.target.value=P().branch_id}
 }
});
