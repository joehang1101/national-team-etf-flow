document.querySelectorAll('[data-filter-table]').forEach(function(input){
  var table=document.getElementById(input.dataset.filterTable);
  if(!table)return;
  var select=document.querySelector('[data-signal-table="'+table.id+'"]');
  var counter=document.querySelector('[data-filter-count="'+table.id+'"]');
  function update(){
    var query=input.value.trim().toLowerCase(),shown=0;
    table.querySelectorAll('tbody tr[data-search]').forEach(function(row){
      var match=(!query||row.dataset.search.toLowerCase().includes(query))&&(!select||select.value==='all'||row.dataset.signal===select.value);
      row.hidden=!match;if(match)shown++;
    });
    if(counter)counter.textContent='显示 '+shown+' 项';
  }
  input.addEventListener('input',update);
  if(select)select.addEventListener('change',update);
  update();
});
