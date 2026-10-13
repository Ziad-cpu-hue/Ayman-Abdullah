(function(){
  // إخفاء التنبيهات تلقائيًا
  setTimeout(function(){document.querySelectorAll('.toast').forEach(function(t){t.style.transition='opacity .5s';t.style.opacity='0';setTimeout(function(){t.remove()},600)})},5200);
  // القائمة الجانبية في الشاشات الصغيرة
  var sb=document.getElementById('ui-sidebar'),ov=document.getElementById('ui-overlay');
  document.querySelectorAll('[data-toggle-sidebar]').forEach(function(b){b.addEventListener('click',function(){
    if(!sb)return; sb.classList.toggle('translate-x-full'); if(ov)ov.classList.toggle('hidden');
  })});
  if(ov)ov.addEventListener('click',function(){sb.classList.add('translate-x-full');ov.classList.add('hidden')});
  // نسخ نص
  document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){
    var v=b.getAttribute('data-copy');var old=b.innerHTML;
    var done=function(){b.innerHTML='<i class="fa-solid fa-check"></i> تم النسخ';setTimeout(function(){b.innerHTML=old},1600)};
    if(navigator.clipboard){navigator.clipboard.writeText(v).then(done)}else{var t=document.createElement('textarea');t.value=v;document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();done()}
  })});
  // تصفية بطاقات بالبيانات: data-filter-group + data-filter-value على الأزرار، data-f-<group> على العناصر
  var groups={};
  document.querySelectorAll('[data-filter-group]').forEach(function(btn){
    var g=btn.getAttribute('data-filter-group');
    btn.addEventListener('click',function(){
      document.querySelectorAll('[data-filter-group="'+g+'"]').forEach(function(x){x.classList.remove('is-on')});
      btn.classList.add('is-on');groups[g]=btn.getAttribute('data-filter-value');applyFilters();
    });
    if(btn.classList.contains('is-on'))groups[g]=btn.getAttribute('data-filter-value');
  });
  var q=document.querySelector('[data-filter-search]');
  if(q)q.addEventListener('input',applyFilters);
  function applyFilters(){
    var term=q?q.value.trim().toLowerCase():'';
    var items=document.querySelectorAll('[data-filter-item]'),shown=0;
    items.forEach(function(it){
      var ok=true;
      Object.keys(groups).forEach(function(g){var v=groups[g],iv=it.getAttribute('data-f-'+g);if(v&&v!=='all'&&iv!==v&&iv!=='*')ok=false});
      if(ok&&term&&(it.getAttribute('data-search')||it.textContent).toLowerCase().indexOf(term)===-1)ok=false;
      it.classList.toggle('hidden',!ok);if(ok)shown++;
    });
    var em=document.querySelector('[data-filter-empty]');if(em)em.classList.toggle('hidden',shown>0);
  }
  if(document.querySelector('[data-filter-item]'))applyFilters();
  // سحب وإفلات الملفات
  document.querySelectorAll('.dropzone').forEach(function(z){
    var inp=z.querySelector('input[type=file]');if(!inp)return;
    ['dragenter','dragover'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.add('drag')})});
    ['dragleave','drop'].forEach(function(e){z.addEventListener(e,function(ev){ev.preventDefault();z.classList.remove('drag')})});
    z.addEventListener('drop',function(ev){if(ev.dataTransfer&&ev.dataTransfer.files.length){inp.files=ev.dataTransfer.files;inp.dispatchEvent(new Event('change'))}});
    inp.addEventListener('change',function(){var out=z.querySelector('[data-file-names]');if(out){out.textContent=Array.prototype.map.call(inp.files,function(f){return f.name}).join('، ')}});
  });
})();
// بطاقات المفردات القلّابة
document.addEventListener('click',function(e){
  if(e.target.closest('[data-speak]'))return;
  var c=e.target.closest('[data-flip]');
  if(c){c.querySelectorAll('[data-face]').forEach(function(f){f.classList.toggle('hidden')});}
});
