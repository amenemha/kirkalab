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
await p.waitForSelector('.kpis',{timeout:30000});await p.waitForTimeout(2500);
const ovf=async(n)=>{const w=await p.evaluate(()=>[document.documentElement.scrollWidth,innerWidth]);ok(n+': нет горизонтального вылета',w[0]<=w[1]+1,w.join('/'));};
const shot=async(n)=>p.screenshot({path:`/tmp/e_${n}.png`});
// today
const t0=Date.now();await p.click('.tab[data-page=asics]');await p.waitForSelector('.chip[data-st]');ok('Асики открываются < 3 с',Date.now()-t0<3000,(Date.now()-t0)+' мс');const t1=Date.now();await p.locator('.chip[data-st="offline"]').click();await p.waitForTimeout(50);await p.waitForFunction(()=>document.querySelector('.chip[data-st="offline"]').classList.contains('on'));ok('Фильтр срабатывает < 1 с',Date.now()-t1<1000,(Date.now()-t1)+' мс');await p.click('.tab[data-page=today]');await p.waitForSelector('#tC');await p.waitForTimeout(1500);
ok('Обзор: KPI',await p.locator('.kpis .k').count()>=4);ok('Обзор: график',await p.locator('.chartbox svg').count()>=1);
ok('Обзор: таблица контейнеров',await p.locator('#tC tr[data-cont]').count()>=20,String(await p.locator('#tC tr[data-cont]').count()));
for(const h of ['6','72','168']){await p.click(`#chRange button[data-h="${h}"]`);await p.waitForTimeout(1200);}
ok('Обзор: смена диапазона графика',await p.locator('.chartbox svg path.l').count()>=1);
const cb=await p.locator('.chartbox').first().boundingBox();await p.mouse.move(cb.x+cb.width/2,cb.y+cb.height/2);await p.waitForTimeout(300);
ok('Обзор: подсказка на графике',await p.locator('.ctip').first().isVisible());
await ovf('Обзор');await shot('today');
// map
await p.click('.tab[data-page=map]');await p.waitForSelector('.cbox[data-c]');await p.waitForTimeout(800);
const nC=await p.locator('.cbox[data-c]').count();ok('Контейнеры: карточки',nC>=20,String(nC));
for(const l of await p.locator('#mLayer button').all()){await l.click();await p.waitForTimeout(300);} 
await p.locator('#mLayer button').first().click();await ovf('Контейнеры');await shot('map');
// container: find hydro + air
const names=await p.$$eval('.cbox[data-c]',els=>els.map(e=>[e.dataset.c,e.textContent]));
const air=names.find(x=>/5а 8-9|4к 6-7/.test(x[1]))||names[0];const hyd=names.find(x=>/hydro Bitmain/.test(x[1]));
await p.click(`.cbox[data-c="${hyd[0]}"]`);await p.waitForSelector('.pc');await p.waitForTimeout(800);
ok('Гидро: два кабинета',(await p.locator('.sec2').count())>=2);await ovf('Контейнер гидро');await shot('cont_hydro');
await p.goto('https://kirkalab.ru/#cont/'+air[0]);await p.waitForTimeout(500);await p.reload();await p.waitForSelector('.pc[data-id]');await p.waitForTimeout(800);
ok('Воздух: стеллажи',await p.locator('.rack').count()>=5,String(await p.locator('.rack').count()));
ok('Воздух: заголовок стеллажа',(await p.locator('.rk').first().textContent()).includes('Стеллаж'));
// tooltip
await p.locator('.pc[data-id]').nth(3).hover();await p.waitForTimeout(300);ok('Подсказка на месте',await p.locator('#tip').isVisible());
// selection: select 6, unselect 3 -> 3
await p.check('#selMode');await p.waitForTimeout(300);
const pcs=p.locator('.pc[data-id]');const ids=[];for(let i=0;i<6;i++){await pcs.nth(i*2).click();ids.push(await pcs.nth(i*2).getAttribute('data-id'));}
for(const i of [0,2,4]){await pcs.nth(i*2).click();}
const want=[ids[1],ids[3],ids[5]].map(Number).sort((a,b)=>a-b);
ok('Выделение: счётчик = 3',(await p.textContent('#selN')).trim()==='3',await p.textContent('#selN'));
await shot('cont_sel');
const [rq]=await Promise.all([p.waitForRequest(r=>r.url().includes('/devices/export.xlsx')),p.click('#selbar [data-act=export]')]);
const body=JSON.parse(rq.postData()).ids.sort((a,b)=>a-b);ok('Выгрузка: ровно выделенные',JSON.stringify(body)===JSON.stringify(want),JSON.stringify(body)+' vs '+JSON.stringify(want));
const dl=await p.waitForEvent('download',{timeout:20000}).catch(()=>null);
if(dl){await dl.saveAs('/tmp/e_sel.xlsx');ok('Выгрузка: файл скачан',true,dl.suggestedFilename());}else ok('Выгрузка: файл скачан',false);
// rack selection
await p.click('#selbar [data-act=clear]');await p.locator('.rk').first().click();await p.waitForTimeout(300);
const nr=+(await p.textContent('#selN')).replace(/\D/g,'');ok('Клик по стеллажу выделяет стеллаж',nr>=10,String(nr));
await p.click('#selbar [data-act=list]');await p.waitForSelector('#idsX');await p.waitForTimeout(500);
ok('«Показать списком» → асики с этим набором',await p.locator('#tA tr[data-id], .tbl tr[data-id]').count()===nr,String(await p.locator('.tbl tr[data-id]').count()));
await p.click('#selbar [data-act=clear]').catch(()=>{});
// layout editor open/close
await p.goto('https://kirkalab.ru/#cont/'+air[0]);await p.waitForSelector('#lEdit');await p.click('#lEdit');await p.waitForTimeout(400);
ok('Редактор раскладки открывается',await p.locator('#modal').isVisible());await shot('cont_edit');await p.keyboard.press('Escape');
// asics: working chip + container filter
await p.click('.tab[data-page=asics]');await p.waitForSelector('.chip[data-st]');await p.waitForTimeout(600);
await p.selectOption('#fCont',{index:3});await p.waitForTimeout(1200);const cAll=+(await p.locator('.chip[data-st=""] b').textContent()).replace(/\D/g,'');ok('Асики: контейнер сужает счётчики',cAll<1500,String(cAll));
const w=p.locator('.chip[data-st="working"]');const wn=+(await w.locator('b').textContent()).replace(/\D/g,'');await w.click();await p.waitForTimeout(1000);
const rows=await p.locator('.tbl tr[data-id]').count();ok('Асики: «Работают» + контейнер не пусто',wn===0||rows>0,`chip=${wn} rows=${rows}`);
await ovf('Асики');await shot('asics');
await p.locator('.chip[data-st=""]').click();await p.waitForTimeout(500);
await p.selectOption('#fCont',{index:0});await p.waitForTimeout(800);const r0=await p.locator('#tA tbody tr').count();for(let i=0;i<4;i++){await p.$eval('#tA',t=>{const sc=t.closest('.scroll');sc.scrollTop=sc.scrollHeight});await p.waitForTimeout(400);}
const r1=await p.locator('#tA tbody tr').count();ok('Асики: бесконечная прокрутка догружает',r1>r0,r0+' → '+r1);
// table checkbox select/unselect + excel
await p.click('.tab[data-page=asics]');await p.waitForTimeout(800);
const cbs=p.locator('.tbl tr[data-id] input[type=checkbox]');for(let i=0;i<5;i++)await cbs.nth(i).check();await cbs.nth(1).uncheck();await cbs.nth(3).uncheck();
ok('Асики: чекбоксы 5−2 = 3',(await p.textContent('#selN')).trim()==='3');
const [rq2]=await Promise.all([p.waitForRequest(r=>r.url().includes('/devices/export.xlsx')),p.click('#selbar [data-act=export]')]);
ok('Асики: выгрузка = 3 строки',JSON.parse(rq2.postData()).ids.length===3);await p.waitForTimeout(1500);
await p.click('#selbar [data-act=clear]');
// errors
await p.click('.tab[data-page=errors]');await p.waitForSelector('.clsc');await p.waitForTimeout(800);
ok('Проблемы: карточки классов',await p.locator('.clsc').count()>=3);
await p.locator('.clsc').nth(1).click();await p.waitForTimeout(700);ok('Проблемы: фильтр по классу',await p.locator('.tbl tr[data-id]').count()>0);
await p.locator('#tG tr[data-gk]').first().click().catch(()=>{});await p.waitForTimeout(600);
await ovf('Проблемы');await shot('errors');
// clients, pools
await p.click('.tab[data-page=clients]');await p.waitForSelector('tr[data-client]',{timeout:20000}).catch(()=>{});ok('Клиенты: таблица',await p.locator('tr[data-client]').count()>0);await ovf('Клиенты');await shot('clients');
await p.click('.tab[data-page=pools]');await p.waitForSelector('#tP',{timeout:30000});await p.waitForTimeout(800);ok('Пулы: таблица',await p.locator('#tP tr[data-pacc]').count()>0);await ovf('Пулы');await shot('pools');
// global search
const dev=await p.evaluate(()=>S.devs.find(d=>d.ip&&d.status==='working'));
await p.fill('#gq',dev.ip);await p.waitForTimeout(900);ok('Поиск: есть результат',await p.locator('.gr[data-gd]').count()>0);
await p.keyboard.press('Enter');await p.waitForTimeout(1500);ok('Поиск → карточка асика',await p.locator('#drawer').isVisible());
ok('Карточка: место указано',/стеллаж|полка|№|место/.test(await p.textContent('#drIn')));await shot('card');await p.keyboard.press('Escape');
// theme light
await p.click('#themeBtn');await p.click('.tab[data-page=today]');await p.waitForTimeout(2000);await shot('today_light');
await p.goto('https://kirkalab.ru/#cont/'+hyd[0]);await p.reload();await p.waitForTimeout(3000);await shot('cont_light');await p.click('#themeBtn');
// mobile
await p.setViewportSize({width:390,height:844});
for(const pg of ['today','map','asics','errors']){await p.click(`.tab[data-page=${pg}]`);await p.waitForTimeout(1800);await ovf('Телефон '+pg);await shot('m_'+pg);}
ok('Ошибок в консоли нет',errs.length===0,errs.slice(0,5).join(' | '));
console.log(res.join('\n'));await b.close();
