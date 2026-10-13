(function(){
  var form=document.getElementById('quizForm');if(!form)return;
  var items=[].slice.call(form.querySelectorAll('.qitem'));
  var dots=document.getElementById('qdots');
  var bar=document.getElementById('qbar');
  var cnt=document.getElementById('qcount');
  var prev=document.getElementById('qprev'),next=document.getElementById('qnext'),sub=document.getElementById('qsubmit');
  var cur=0,submitted=false;
  var qcur=document.getElementById('qcur');
  var DOT='h-12 rounded-2xl font-num font-bold text-base flex items-center justify-center transition-all hover:scale-105 active:scale-95 shadow-sm ';
  var DOT_IDLE='bg-[#edf3fc] hover:bg-[#e1ecfb] text-[#2b5997]',DOT_DONE='bg-emerald-100 hover:bg-emerald-200 text-emerald-700',
      DOT_CUR='bg-[#245ce6] text-white font-extrabold shadow-lg shadow-blue-500/35 ring-2 ring-blue-400 ring-offset-2 scale-105';
  items.forEach(function(it,i){var d=document.createElement('button');d.type='button';d.className=DOT;d.textContent=i+1;d.onclick=function(){go(i);};dots.appendChild(d);
    it.querySelectorAll('input[type=radio]').forEach(function(r){r.addEventListener('change',function(){mark();});});
    it.querySelectorAll('textarea.essay-input').forEach(function(t){t.addEventListener('input',function(){mark();});});});
  function answered(i){if(items[i].querySelector('input:checked'))return true;var t=items[i].querySelector('textarea.essay-input');return !!(t&&t.value.trim());}
  function mark(){[].forEach.call(dots.children,function(d,i){d.className=DOT+(i===cur?DOT_CUR:(answered(i)?DOT_DONE:DOT_IDLE));});
    var n=items.filter(function(_,i){return answered(i);}).length;bar.style.width=Math.round((cur+1)*100/items.length)+'%';
    if(qcur)qcur.textContent=cur+1;
    cnt.textContent='تمت الإجابة عن '+n+' من '+items.length;}
  function go(i){cur=Math.max(0,Math.min(items.length-1,i));items.forEach(function(it,k){it.classList.toggle('hidden',k!==cur);it.classList.toggle('flex',k===cur);});
    prev.disabled=cur===0;var last=cur===items.length-1;next.classList.toggle('hidden',last);sub.classList.toggle('hidden',!last);sub.classList.toggle('flex',last);mark();window.scrollTo({top:0,behavior:'smooth'});}
  prev.onclick=function(){go(cur-1);};next.onclick=function(){go(cur+1);};
  form.addEventListener('submit',function(e){
    if(submitted)return;var un=items.length-items.filter(function(_,i){return answered(i);}).length;
    if(un&&!form.dataset.timeout&&!confirm('لم تُجب عن '+un+' سؤال. هل تريد التسليم الآن؟')){e.preventDefault();return;}
    submitted=true;});
  window.addEventListener('beforeunload',function(e){if(!submitted&&!form.dataset.preview){e.preventDefault();e.returnValue='';}});
  // المؤقّت
  var secs=parseInt(form.getAttribute('data-seconds')||'0',10);var tm=document.getElementById('qtimer');
  if(secs>0&&tm){var end=Date.now()+secs*1000;var t=setInterval(function(){var left=Math.max(0,Math.round((end-Date.now())/1000));
      var m=Math.floor(left/60),s=left%60;tm.textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;if(left<=60)tm.style.color='#d64545';
      if(left<=0){clearInterval(t);form.dataset.timeout='1';submitted=true;form.submit();}},500);}
  go(0);
})();
