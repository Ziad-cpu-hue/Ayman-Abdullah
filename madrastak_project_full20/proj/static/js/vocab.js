(function(){
  var dataEl=document.getElementById('vocab-data');if(!dataEl)return;
  var words=JSON.parse(dataEl.textContent);var mode=document.getElementById('vocab-app').getAttribute('data-mode');
  var recordUrl=document.getElementById('vocab-app').getAttribute('data-record');
  var csrf=(document.cookie.match(/csrftoken=([^;]+)/)||[])[1]||'';
  var app=document.getElementById('vocab-stage');var i=0,score=0,results=[];
  function shuffle(a){a=a.slice();for(var k=a.length-1;k>0;k--){var j=Math.floor(Math.random()*(k+1));var t=a[k];a[k]=a[j];a[j]=t;}return a;}
  var order=shuffle(words);
  function opts(w,key){var others=shuffle(words.filter(function(x){return x.id!==w.id;})).slice(0,3);return shuffle(others.concat([w])).map(function(x){return x;});}
  function esc(s){var d=document.createElement('div');d.textContent=s;return d.innerHTML;}
  function kind(){if(mode==='test')return ['choose','write','listen'][i%3];return mode;}
  function show(){
    if(i>=order.length)return finish();
    var w=order[i],k=kind(),h='<div class="card"><div class="row spread"><b>'+(i+1)+' / '+order.length+'</b><span class="badge">'+({choose:'اختر المعنى',listen:'استمع واختر الكلمة',write:'اكتب الكلمة بالإنجليزية'})[k]+'</span></div><div class="progress mt"><i style="width:'+(i*100/order.length)+'%"></i></div><div class="center mt">';
    if(k==='choose'){h+='<div class="word">'+esc(w.word)+'</div><button class="speak" type="button" onclick="speakWord(\''+w.word.replace(/'/g,"\\'")+'\')">🔊</button><div class="mt">';
      opts(w).forEach(function(o){h+='<button class="btn ghost block mb opt" data-id="'+o.id+'" type="button">'+esc(o.meaning)+'</button>';});h+='</div>';}
    else if(k==='listen'){h+='<button class="speak" style="width:70px;height:70px;font-size:1.8rem" type="button" onclick="speakWord(\''+w.word.replace(/'/g,"\\'")+'\')">🔊</button><p class="muted">اضغط للاستماع ثم اختر الكلمة الصحيحة</p><div class="mt">';
      opts(w).forEach(function(o){h+='<button class="btn ghost block mb opt ltr" style="direction:ltr" data-id="'+o.id+'" type="button">'+esc(o.word)+'</button>';});h+='</div>';}
    else{h+='<div class="word" style="direction:rtl">'+esc(w.meaning)+'</div><div class="field mt"><input class="input ltr" id="wr" style="text-align:center;direction:ltr" autocomplete="off" autocapitalize="off"></div><button class="btn" id="wrok" type="button">تأكيد</button>';}
    h+='<div id="fb" class="mt"></div></div></div>';app.innerHTML=h;
    if(k==='write'){var inp=document.getElementById('wr');inp.focus();var ok=function(){answer(inp.value.trim().toLowerCase()===w.word.toLowerCase(),k,w);};
      document.getElementById('wrok').onclick=ok;inp.onkeydown=function(e){if(e.key==='Enter')ok();};}
    else{app.querySelectorAll('.opt').forEach(function(b){b.onclick=function(){answer(parseInt(b.dataset.id)===w.id,k,w,b);};});}
    if(k==='listen'||(k==='choose'&&mode!=='test')){setTimeout(function(){window.speakWord(w.word);},250);}
  }
  function answer(ok,k,w,btn){
    if(app.dataset.lock)return;app.dataset.lock='1';results.push({id:w.id,ok:ok});if(ok)score++;
    var feedback=mode!=='test';var fb=document.getElementById('fb');
    if(feedback){if(btn)btn.classList.add(ok?'ok':'bad');app.querySelectorAll('.opt').forEach(function(b){if(parseInt(b.dataset.id)===w.id)b.style.borderColor='#1f9d61';});
      fb.innerHTML=(ok?'<div class="right">✅ إجابة صحيحة</div>':'<div class="reason">❌ الصحيح: <b class="ltr">'+esc(w.word)+'</b> = '+esc(w.meaning)+'</div>')+'<div class="muted small">'+esc(w.example||'')+'</div><button class="btn mt" id="nx" type="button">التالي</button>';
      document.getElementById('nx').onclick=function(){delete app.dataset.lock;i++;show();};}
    else{delete app.dataset.lock;i++;show();}
  }
  function finish(){
    var wrong=results.filter(function(r){return !r.ok;});
    app.innerHTML='<div class="card center"><div class="scorering" style="--p:'+Math.round(score*100/order.length)+'"><div>'+score+'/'+order.length+'</div></div><h3 class="mt">'+(wrong.length?'راجع الكلمات التي أخطأت فيها':'ممتاز! أجبت عن كل الكلمات صحيحًا')+'</h3><a class="btn" href="">إعادة المحاولة</a></div>'
      +(wrong.length?'<div class="card mt"><h4>كلمات للمراجعة</h4>'+wrong.map(function(r){var w=words.filter(function(x){return x.id===r.id;})[0];return '<div class="row spread mb"><b class="ltr">'+esc(w.word)+'</b><span>'+esc(w.meaning)+'</span></div>';}).join('')+'</div>':'');
    fetch(recordUrl,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf},body:JSON.stringify({results:results})});
  }
  if(!order.length){app.innerHTML='<div class="empty">لا توجد كلمات متاحة.</div>';}else show();
})();
