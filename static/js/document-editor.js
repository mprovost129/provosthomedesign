(() => {
  'use strict';
  function hundredths(value, digits) {
    const text=String(value ?? '').trim();
    if (!new RegExp('^(?:\\d{1,'+digits+'}(?:\\.\\d{0,2})?|\\.\\d{1,2})$').test(text)) throw new Error('Use numbers with up to two decimal places.');
    const [whole,fraction='']=text.split('.');
    return BigInt(whole||'0')*100n+BigInt((fraction+'00').slice(0,2));
  }
  function lineCents(quantity,rate) {
    const q=hundredths(quantity,6),r=hundredths(rate,8);
    if(q<=0n)throw new Error('Enter a positive quantity.');
    return (q*r+50n)/100n;
  }
  function money(cents) {return '$'+(cents/100n).toLocaleString('en-US')+'.'+String(cents%100n).padStart(2,'0');}
  if(typeof module!=='undefined'&&module.exports){module.exports={hundredths,lineCents,money};return;}
  const form=document.querySelector('[data-document-editor]');if(!form)return;
  const field=name=>form.elements.namedItem(name);
  const parse=id=>JSON.parse(document.getElementById(id).textContent);
  const clients=parse('document-client-data'), project=parse('document-project-data'), issuer=parse('document-issuer-data'), rich=parse('document-rich-data');
  const list=form.querySelector('[data-doc-lines]'), template=form.querySelector('[data-doc-empty]');
  const sectionLabels={scope:'Scope of work',deliverables:'Deliverables',revisions:'Included revisions',exclusions:'Exclusions',timeline:'Expected timing',notes:'Notes',terms:'Payment and other terms'};
  const editors=new Map();let saving=false;
  const status=form.querySelector('[data-doc-save-status]');
  function text(selector,value){form.querySelector(selector).textContent=value||'—';}
  function sanitizeFragment(value) {
    const parsed=new DOMParser().parseFromString(value,'text/html');
    const fragment=document.createDocumentFragment(), allowed=new Set(['P','BR','STRONG','B','EM','I','U','UL','OL','LI','BLOCKQUOTE','DIV']);
    function copy(node,parent){
      if(node.nodeType===Node.TEXT_NODE){parent.append(document.createTextNode(node.textContent));return;}
      if(node.nodeType!==Node.ELEMENT_NODE||['SCRIPT','STYLE','IFRAME','SVG','MATH','OBJECT','TEMPLATE'].includes(node.tagName))return;
      const out=allowed.has(node.tagName)?document.createElement(node.tagName.toLowerCase()):parent;
      if(out!==parent)parent.append(out);
      for(const child of node.childNodes)copy(child,out);
    }
    for(const child of parsed.body.childNodes)copy(child,fragment);
    return fragment;
  }
  for(const wrapper of form.querySelectorAll('[data-rich-field]')) {
    const name=wrapper.dataset.richField, original=field(name), hidden=field(name+'_html');
    const bar=document.createElement('div');bar.className='doc-format-bar';bar.setAttribute('role','toolbar');bar.setAttribute('aria-label',sectionLabels[name]+' formatting');
    const editor=document.createElement('div');editor.className='doc-rich-editor';editor.contentEditable='true';editor.setAttribute('role','textbox');editor.setAttribute('aria-multiline','true');editor.setAttribute('aria-label',sectionLabels[name]+' editor');
    if(rich[name])editor.append(sanitizeFragment(rich[name]));else editor.textContent=original.value;
    for(const [label,command] of [['Bold','bold'],['Italic','italic'],['Underline','underline'],['Bullets','insertUnorderedList'],['Numbered list','insertOrderedList']]){
      const button=document.createElement('button');button.type='button';button.textContent=label;
      button.addEventListener('mousedown',event=>event.preventDefault());
      button.addEventListener('click',()=>{editor.focus();document.execCommand(command,false,null);sync();render();});bar.append(button);
    }
    function sync(){original.value=editor.innerText;const safe=document.createElement('div');safe.append(sanitizeFragment(editor.innerHTML));hidden.value=safe.innerHTML;}
    editor.addEventListener('input',()=>{sync();render();});
    editor.addEventListener('paste',event=>{event.preventDefault();document.execCommand('insertText',false,event.clipboardData.getData('text/plain'));sync();render();});
    editor.addEventListener('drop',event=>event.preventDefault());
    editor.addEventListener('blur',sync);
    original.hidden=true;original.required=false;wrapper.querySelector('[data-rich-hint]').hidden=true;
    wrapper.append(bar,editor);editors.set(name,{editor,original,hidden,sync});
    // Leave the canonical values unchanged on load so a blank draft is clean.
  }
  function orderRows(){
    const rows=[...list.querySelectorAll('[data-doc-line]')];
    rows.forEach((row,index)=>{row.querySelector('[data-line-label]').textContent='Service '+(index+1);row.querySelector('input[name$="-ORDER"]').value=index;row.querySelector('[data-line-up]').disabled=index===0;row.querySelector('[data-line-down]').disabled=index===rows.length-1;});
  }
  function wire(row){
    row.querySelector('[data-line-up]').addEventListener('click',()=>{if(row.previousElementSibling)list.insertBefore(row,row.previousElementSibling);orderRows();render();});
    row.querySelector('[data-line-down]').addEventListener('click',()=>{if(row.nextElementSibling)list.insertBefore(row.nextElementSibling,row);orderRows();render();});
  }
  list.querySelectorAll('[data-doc-line]').forEach(wire);orderRows();
  form.querySelector('[data-doc-add]').addEventListener('click',()=>{
    const count=Number(field('lines-TOTAL_FORMS').value);
    if(count>=20){status.textContent='Use up to 20 service lines. Reuse or edit an existing line.';return;}
    const holder=document.createElement('div');holder.innerHTML=template.innerHTML.replaceAll('__prefix__',String(count));
    const row=holder.firstElementChild;list.append(row);field('lines-TOTAL_FORMS').value=count+1;wire(row);orderRows();render();row.querySelector('textarea').focus();
  });
  function render(){
    const kind=field('document_kind').value||'proposal', invoice=kind==='invoice';
    const client=clients.find(c=>c.id===field('client').value);
    text('[data-preview-kind]',invoice?'Invoice draft':kind==='estimate'?'Estimate':'Proposal');
    text('[data-preview-title]',field('title').value||'Your project');text('[data-preview-client]',client?.name||'Choose a client');text('[data-preview-address]',client?.address||'Billing address not recorded');
    const clientLink=form.querySelector('[data-client-record]');if(client)clientLink.href=client.url;
    text('[data-preview-project]',project.name);text('[data-preview-project-address]',project.address);
    text('[data-preview-reference]',field('document_reference').value||project.reference||'');
    text('[data-preview-issuer]',[issuer.address,issuer.email,issuer.phone].filter(Boolean).join('\n'));
    text('[data-preview-issued]',field('issue_date').value);text('[data-preview-expiry]',field(invoice?'due_date':'valid_until').value);
    text('[data-preview-expiry-label]',invoice?'Proposed due date':'Valid through');
    const body=form.querySelector('[data-preview-lines]');body.replaceChildren();let total=0n,valid=true,count=0;
    for(const row of list.querySelectorAll('[data-doc-line]')){
      const deleted=row.querySelector('input[name$="-DELETE"]').checked;row.classList.toggle('removed',deleted);
      for(const input of row.querySelectorAll('textarea,input:not([type=hidden]):not([type=checkbox])'))input.required=!deleted;
      if(deleted)continue;
      const description=row.querySelector('textarea').value,quantity=row.querySelector('input[name$="-quantity"]').value,rate=row.querySelector('input[name$="-rate"]').value;
      let amount='—';try{const cents=lineCents(quantity,rate);total+=cents;amount=money(cents);}catch(_){valid=false;}
      row.querySelector('[data-line-amount]').textContent=amount;
      const tr=document.createElement('tr');for(const value of [description||'Service description',quantity||'—',rate?'$'+rate:'—',amount]){const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);count++;
    }
    if(!count||total>99999999999n)valid=false;
    const totalText=valid?money(total):'Review service quantities and rates';text('[data-doc-total]',totalText);text('[data-preview-total]',totalText);
    const head=form.querySelector('[data-preview-scope]'),tail=form.querySelector('[data-preview-tail]');head.replaceChildren();tail.replaceChildren();
    for(const [name,config] of editors){
      const value=config.original.value,content=document.createElement('section');content.className='doc-prose';
      const required=kind==='proposal'&&['scope','deliverables'].includes(name);
      config.editor.setAttribute('aria-required',String(required));
      if(!value.trim())continue;
      const heading=document.createElement('h3');heading.textContent=sectionLabels[name];content.append(heading,sanitizeFragment(config.editor.innerHTML));
      (['terms','notes'].includes(name)?tail:head).append(content);
    }
  }
  form.addEventListener('input',render);form.addEventListener('change',render);render();
  const baseline=()=>new URLSearchParams(new FormData(form)).toString();const original=baseline();
  form.addEventListener('submit',event=>{
    for(const [name,config] of editors){config.sync();if(field('document_kind').value==='proposal'&&['scope','deliverables'].includes(name)&&!config.original.value.trim()){event.preventDefault();status.textContent='Add scope and deliverables before saving a proposal.';config.editor.focus();return;}}
    saving=true;
  });
  window.addEventListener('pageshow',()=>{saving=false;});
  window.addEventListener('beforeunload',event=>{if(!saving&&baseline()!==original){event.preventDefault();event.returnValue='';}});
})();
