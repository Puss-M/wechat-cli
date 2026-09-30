(function(){
  const records=(window.MEMORY_DATA&&window.MEMORY_DATA.records)||[];
  const dateOf=r=>String(r.created_at_local||'').slice(0,10);
  const yearOf=r=>dateOf(r).slice(0,4)||'未知';
  const years=[...new Set(records.map(yearOf).filter(y=>/^\d{4}$/.test(y)))].sort().reverse();
  const current=years[0]||new Date().getFullYear().toString();
  const countImages=records.reduce((n,r)=>n+(Array.isArray(r.images)?r.images.length:0),0);
  const dated=records.filter(r=>/^\d{4}-\d{2}-\d{2}/.test(dateOf(r)));
  const active=new Set(dated.map(dateOf)).size;
  const places={};
  records.forEach(r=>{const l=r.location||{};const p=[l.city,l.poiName,l.poiAddress].filter(Boolean).join(' · ');if(p) places[p]=(places[p]||0)+1});
  const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sorted=[...records].sort((a,b)=>String(b.created_at_epoch||b.created_at_local||'').localeCompare(String(a.created_at_epoch||a.created_at_local||'')));
  const assetOf=r=>{const a=Array.isArray(r.archive_assets)?r.archive_assets.find(x=>x&&x.status==='available'&&x.relative_path):null;return a?`../${a.relative_path}`:null};
  document.querySelector('#edition').textContent=years.length?`LOCAL EDITION / ${years[0]}`:'LOCAL EDITION';
  document.querySelector('#hero-year').textContent=years[0]||'—';
  document.querySelector('#hero-dek').textContent=`从本机归档里，保留下来的不是一个完整答案，而是一段可以继续补写的生活。当前记录覆盖 ${years.length?years[years.length-1]+' 至 '+years[0]:'未知范围'}。`;
  document.querySelector('#hero-facts').innerHTML=[['记录',records.length],['活跃日',active],['图片条目',countImages],['地点',Object.keys(places).length]].map(x=>`<div class="fact"><b>${x[1]}</b><span>${x[0]}</span></div>`).join('');
  const cover=sorted.find(r=>assetOf(r)); if(cover){const src=assetOf(cover);document.querySelector('#hero-image').innerHTML=`<img src="${src}" alt="${esc(cover.text||'年度记忆')}" loading="eager"><span class="image-caption">${esc(cover.created_at_local||'')}</span>`}
  document.querySelector('#coverage').innerHTML=`<strong>本地档案状态</strong><span>已归档 ${records.length} 条 / ${years.length?years[years.length-1]+' — '+years[0]:'时间范围未知'} / 图片按本人缓存归属解码</span>`;
  function renderMonths(year){const counts={};const picks={};records.forEach(r=>{if(yearOf(r)===year){const m=dateOf(r).slice(5,7);if(m){counts[m]=(counts[m]||0)+1;if(!picks[m]&&assetOf(r))picks[m]=assetOf(r)}}});document.querySelector('#months').innerHTML=Array.from({length:12},(_,i)=>{const m=String(i+1).padStart(2,'0'),n=counts[m]||0;return `<article class="month ${n?'':'uncovered'}">${picks[m]?`<img src="${picks[m]}" alt="${m} 月图片" loading="lazy">`:''}<span class="state"></span><a class="month-link" href="#recent"><h3>${m} 月</h3><span class="count">${n?`${n} 条记录`:'未覆盖或暂无记录'}</span></a></article>`}).join('')}
  document.querySelector('#year-tabs').innerHTML=(years.length?years:[current]).map(y=>`<button class="${y===current?'active':''}" data-year="${y}">${y}</button>`).join('');
  const reportLink=document.querySelector('#annual-report-link');reportLink.href=`annual-report.html?year=${current}`;
  document.querySelectorAll('#year-tabs button').forEach(b=>b.addEventListener('click',()=>{document.querySelectorAll('#year-tabs button').forEach(x=>x.classList.remove('active'));b.classList.add('active');renderMonths(b.dataset.year);reportLink.href=`annual-report.html?year=${b.dataset.year}`}));renderMonths(current);
  document.querySelector('#recent-list').innerHTML=sorted.slice(0,12).map(r=>`<article class="memory">${assetOf(r)?`<img src="${assetOf(r)}" alt="" loading="lazy">`:''}<div class="memory-copy"><span class="memory-date">${esc(r.created_at_local||'时间未知')}</span><p>${esc(r.text||'（无文字记录）')}</p><span class="memory-meta">${esc((r.location||{}).city||'地点未记录')} · ${esc(r.verification||'来源未确认')}</span></div></article>`).join('');
  const placeRows=Object.entries(places).sort((a,b)=>b[1]-a[1]);document.querySelector('#map-list').innerHTML=placeRows.slice(0,12).map(([p,n],i)=>`<div class="map-row"><span>${String(i+1).padStart(2,'0')}</span><b>${esc(p)}</b><em>${n} 条记录</em></div>`).join('')||'<p class="muted">当前归档没有可识别地点。</p>';
  const todayKey=new Date().toISOString().slice(5,10);const todays=sorted.filter(r=>dateOf(r).slice(5,10)===todayKey);document.querySelector('#today-list').innerHTML=todays.length?todays.slice(0,6).map(r=>`<article class="today-card">${assetOf(r)?`<img src="${assetOf(r)}" alt="" loading="lazy">`:''}<div><span class="memory-date">${esc(r.created_at_local||'')}</span><p>${esc(r.text||'（无文字记录）')}</p></div></article>`).join(''):'<p class="empty-note">当前归档里没有“今天”的记录。这里的空白只代表已提取范围内没有匹配，不代表历史上一定没有发布。</p>';
  document.querySelector('#places').innerHTML=Object.entries(places).sort((a,b)=>b[1]-a[1]).slice(0,18).map(([p,n])=>`<div class="place"><b>${esc(p)}</b><span>${n} 条</span></div>`).join('')||'<p class="muted">当前归档没有可识别地点。</p>';
  document.querySelector('#footer-count').textContent=`${records.length} RECORDS / ${countImages} IMAGE ENTRIES`;
})();
