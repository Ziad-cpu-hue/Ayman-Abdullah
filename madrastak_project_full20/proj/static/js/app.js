(function(){
  var b=document.body;
  var mb=document.getElementById('menuBtn');
  if(mb){mb.addEventListener('click',function(){b.classList.toggle('nav-open');});
    document.addEventListener('click',function(e){if(b.classList.contains('nav-open')&&!e.target.closest('.sidebar')&&!e.target.closest('#menuBtn'))b.classList.remove('nav-open');});}
  // إخفاء الرسائل تلقائيًا
  setTimeout(function(){document.querySelectorAll('.flash').forEach(function(f){f.style.transition='opacity .5s';f.style.opacity='0';setTimeout(function(){f.remove();},600);});},5200);
  // تأكيد قبل الإجراءات الحساسة
  document.addEventListener('submit',function(e){var m=e.target.getAttribute('data-confirm');if(m&&!confirm(m))e.preventDefault();});
  document.addEventListener('click',function(e){var a=e.target.closest('a[data-confirm]');if(a&&!confirm(a.getAttribute('data-confirm')))e.preventDefault();});
  // منع تكرار الإرسال
  document.querySelectorAll('form[data-once]').forEach(function(f){f.addEventListener('submit',function(){var s=f.querySelector('button[type=submit]');if(s){setTimeout(function(){s.disabled=true;s.textContent='جارٍ المعالجة...';},10);}});});
  // معاينة الصورة قبل الرفع
  document.querySelectorAll('input[type=file][data-preview]').forEach(function(inp){inp.addEventListener('change',function(){
    var t=document.querySelector(inp.getAttribute('data-preview'));if(!t||!inp.files[0])return;
    t.src=URL.createObjectURL(inp.files[0]);t.classList.remove('hidden');});});
  // تمييز القسم المرتبط بالخطأ
  if(location.hash){var el=document.querySelector(location.hash);if(el){el.classList.add('flash-target');}}
  // تبديل حقول السؤال (صح/خطأ)
  var qt=document.getElementById('id_qtype');
  if(qt){var upd=function(){var tf=qt.value==='tf';document.querySelectorAll('.choice-extra').forEach(function(c){c.classList.toggle('hidden',tf);});
      if(tf){var a=document.querySelector('[name=choice_text_1]'),b2=document.querySelector('[name=choice_text_2]');
        if(a&&!a.value)a.value='صح';if(b2&&!b2.value)b2.value='خطأ';}};
    qt.addEventListener('change',upd);upd();}
  // النطق
  window.speakWord=function(t){if(!('speechSynthesis' in window))return;var u=new SpeechSynthesisUtterance(t);u.lang='en-US';u.rate=.85;speechSynthesis.cancel();speechSynthesis.speak(u);};
  document.querySelectorAll('[data-speak]').forEach(function(x){x.addEventListener('click',function(e){e.stopPropagation();window.speakWord(x.getAttribute('data-speak'));});});
  document.querySelectorAll('.flip').forEach(function(f){f.addEventListener('click',function(){f.classList.toggle('on');});});
})();
