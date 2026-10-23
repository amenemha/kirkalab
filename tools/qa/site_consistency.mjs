import {chromium} from 'playwright';import {execSync} from 'child_process';import fs from 'fs';
const LOCAL=process.env.LOCAL==='1';
const b=await chromium.launch({executablePath:'/home/user/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome'});
const ctx=await b.newContext({viewport:{width:1440,height:900},acceptDownloads:true});const p=await ctx.newPage();
const errs=[];p.on('pageerror',e=>errs.push('PAGE '+e.message));p.on('console',m=>{if(m.type()==='error')errs.push('CONSOLE '+m.text())});
p.on('response',r=>{if(r.url().includes('/api/')&&r.status()>=400&&!r.url().includes('qr/status'))errs.push('HTTP '+r.status()+' '+r.url())});
if(LOCAL)await p.route(/kirkalab\.ru\/(farm\.js|farm\.css|index\.html)?(\?.*)?$/,async r=>{const u=new URL(r.request().url());const f=u.pathname==='/'?'index.html':u.pathname.slice(1);r.fulfill({body:fs.readFileSync('/home/user/workspace/kirkalab/frontend/'+f),contentType:f.endsWith('.js')?'text/javascript':f.endsWith('.css')?'text/css':'text/html'})});
const res=[];const ok=(n,c,info='')=>{res.push((c?'PASS ':'FAIL ')+n+(info?' — '+info:''));};
let logged=false;
for(let t=0;t<4&&!logged;t++){
  const respP=p.waitForResponse(r=>r.url().includes('/auth/qr/start'));
  if(t===0)await p.goto('https://kirkalab.ru/');else await p.reload();
  const s=await (await respP).json();
  const cmd=`docker exec kirkalab-bot-1 python -c 'import httpx,os;print(httpx.post("http://app:8000/api/v1/auth/qr/approve",json={"session_id":"${s.session_id}","telegram_user_id":183868623},headers={"X-Bot-Secret":os.getenv("BOT_INTERNAL_SECRET")}).status_code)'`;
  const code=execSync('ssh -i ~/.ssh/kirka_ed25519 root@85.193.83.240 '+JSON.stringify(cmd)).toString().trim();
  logged=await p.waitForSelector('#app:not([hidden])',{timeout:15000}).then(()=>true).catch(()=>false);
  if(!logged){console.log('login retry, approve='+code);await p.waitForTimeout(t*15000);}
}
await p.waitForSelector('.kpis',{timeout:30000});await p.waitForTimeout(1500);
await p.evaluate(()=>clearInterval(tick));p.setDefaultTimeout(8000);
const num=(t)=>+String(t||'').replace(/[^\d]/g,'');
const optN=(t)=>{const m=String(t).split('·');return num(m[m.length-1]);};
// Wait for a full re-render of #main after an action.
const act=async(fn)=>{try{await act0(fn);}catch(e){bad('действие не выполнилось: '+String(e.message).split('\n')[0]+' | '+await p.evaluate(()=>JSON.stringify(S.f)));}};
const act0=async(fn)=>{await p.evaluate(()=>{const m=document.querySelector('#main');if(m.firstElementChild)m.firstElementChild.dataset.old='1';});await fn();await p.waitForFunction(()=>{const f=document.querySelector('#main').firstElementChild;return f&&!f.dataset.old;},null,{timeout:15000});await p.waitForTimeout(60);};
const opts=async(sel)=>p.$$eval(sel+' option',os=>os.map(o=>[o.value,o.textContent]));
const hintN=async()=>num(await p.textContent('#main .fbar .hint'));
const rowsShown=async(t)=>p.locator(t+' tbody tr[data-id]').count();
let fails=0;const bad=(m)=>{fails++;if(fails<60)res.push('FAIL '+m);};
let checks=0;const chk=(c,m)=>{checks++;if(!c)bad(m);};
// ---------- A. площадка × клиент на «Асиках» ----------
await act(()=>p.click('.tab[data-page=asics]'));
for(const [sv,st] of await opts('#siteSel')){
  await act(()=>p.selectOption('#clientSel',''));
  await act(()=>p.selectOption('#siteSel',sv));
  for(const [cv,ct] of await opts('#clientSel')){
    await act(()=>p.selectOption('#clientSel',cv));
    const got=await p.$eval('#clientSel',s=>s.value);chk(got===cv,`клиент «${ct}» не выбрался при площадке «${st}»`);
    const h=await hintN();chk(h>0,`пусто: «${st}» + «${ct}»`);chk(h===optN(ct),`«${st}» + «${ct}»: в списке ${optN(ct)}, показано ${h}`);
  }
}
await act(()=>p.selectOption('#siteSel',''));await act(()=>p.selectOption('#clientSel',''));
ok('A. Площадка × клиент: каждый вариант = своё число',fails===0,`${checks} проверок`);
// ---------- B. контейнер / модель / статусы для нескольких клиентов ----------
let f0=fails,c0=checks;
const clients=(await opts('#clientSel')).slice(0,5);
for(const [cv,ct] of clients){
  await act(()=>p.selectOption('#clientSel',cv));
  for(const [kv,kt] of (await opts('#fCont'))){
    await act(()=>p.selectOption('#fCont',kv));
    const v=await p.$eval('#fCont',s=>s.value);chk(v===kv,`«${ct}»: контейнер «${kt}» сбросился`);
    const h=await hintN();if(kv)chk(h===optN(kt)&&h>0,`«${ct}» + «${kt}»: в списке ${optN(kt)}, показано ${h}`);
    for(const ch of await p.$$eval('.chip[data-st]:not([disabled]),.chip[data-qk]:not([disabled]),#onlyAl:not([disabled])',cs=>cs.map(c=>c.dataset.st!==undefined?'[data-st="'+c.dataset.st+'"]':c.dataset.qk?'[data-qk="'+c.dataset.qk+'"]':'#onlyAl'))){
      const sel=ch.startsWith('#')?ch:'.chip'+ch;const n=num(await p.textContent(sel+' b'));
      await act(()=>p.click(sel));const h2=await hintN();
      const emp=await p.locator('#main .empty').count();
      chk(h2===n&&(n===0||!emp),`«${ct}» + «${kt}» + «${(await p.textContent(sel)).trim()}»: на кнопке ${n}, показано ${h2}`);
      if(sel!=='.chip[data-st=""]')await act(()=>p.click(sel.startsWith('.chip[data-st')?'.chip[data-st=""]':sel));
    }
  }
  await act(()=>p.selectOption('#fCont',''));
  for(const [mv,mt] of (await opts('#fModel')).slice(1)){
    await act(()=>p.selectOption('#fModel',mv));const h=await hintN();chk(h===optN(mt)&&h>0,`«${ct}» + модель «${mt}»: в списке ${optN(mt)}, показано ${h}`);
  }
  await act(()=>p.selectOption('#fModel',''));
}
await act(()=>p.selectOption('#clientSel',''));
ok('B. Контейнер, модель, статусы: нажатие = число на кнопке',fails===f0,`${checks-c0} проверок`);
// ---------- C. Ваш случай: контейнер, потом клиент без асиков в нём ----------
f0=fails;
{ const all=await opts('#fCont');const k=all.find(x=>/11к 21/.test(x[1]))||all[1];await act(()=>p.selectOption('#fCont',k[0]));
  const pr=(await opts('#clientSel')).find(x=>/Прайм/.test(x[1]))||(await opts('#clientSel'))[2];
  await act(()=>p.selectOption('#clientSel',pr[0]));const h=await hintN();
  chk(h>0,`«${k[1]}» → «${pr[1]}»: «ничего не найдено»`);chk(h===optN(pr[1]),`«${pr[1]}»: ожидалось ${optN(pr[1])}, показано ${h}`);
  await act(()=>p.selectOption('#clientSel',''));}
ok('C. Контейнер → клиент без асиков там: список не пустой',fails===f0);
// ---------- D. Проблемы ----------
f0=fails;c0=checks;
await act(()=>p.click('.tab[data-page=errors]'));
for(const [cv,ct] of clients.slice(0,4)){
  await act(()=>p.selectOption('#clientSel',cv));
  for(const [kv,kt] of (await opts('#eCont')).slice(1)){
    await act(()=>p.selectOption('#eCont',kv));const hdr=await p.textContent('#tE');const w=await p.locator('#tE').evaluate(t=>t.closest('.w').querySelector('.wh').textContent);
    chk(num(w.split('·')[1])===optN(kt),`Проблемы «${ct}» + «${kt}»: в списке ${optN(kt)}, показано ${w.trim()}`);chk(await rowsShown('#tE')>0,`Проблемы «${ct}» + «${kt}»: пусто`);void hdr;
  }
  await act(()=>p.selectOption('#eCont',''));
  for(const c of await p.$$eval('.clsc[data-cls]',cs=>cs.map(c=>c.dataset.cls))){
    await act(()=>p.click(`.clsc[data-cls="${c}"]`));chk(await rowsShown('#tE')>0,`Проблемы «${ct}»: класс ${c||'все'} пустой`);
  }
  await act(()=>p.click('.clsc[data-cls=""]'));
}
await act(()=>p.selectOption('#clientSel',''));
ok('D. Проблемы: каждый контейнер и класс не пустые, числа совпадают',fails===f0,`${checks-c0} проверок`);
// ---------- E. Контейнеры: тип ----------
f0=fails;c0=checks;
await act(()=>p.click('.tab[data-page=map]'));
for(const [cv,ct] of clients){
  await act(()=>p.selectOption('#clientSel',cv));
  for(const k of await p.$$eval('#mKind button',bs=>bs.map(b=>[b.dataset.k,b.textContent]))){
    await act(()=>p.click(`#mKind button[data-k="${k[0]}"]`));const n=await p.locator('.cbox[data-c]').count();
    chk(n>0&&n===num(k[1]),`Контейнеры «${ct}» + «${k[1]}»: на кнопке ${num(k[1])}, карточек ${n}`);
  }
  await act(()=>p.click('#mKind button[data-k=""]'));
}
ok('E. Контейнеры: кнопка типа = число карточек',fails===f0,`${checks-c0} проверок`);
ok('Ошибок в консоли нет',errs.length===0,errs.slice(0,3).join(' | '));
console.log(res.join('\n'));console.log('checks',checks,'fails',fails);
await b.close();
