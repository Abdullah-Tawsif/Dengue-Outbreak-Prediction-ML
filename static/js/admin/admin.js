
const sidebar=document.getElementById('sidebar'),menu=document.getElementById('mobileMenu');if(menu)menu.addEventListener('click',()=>sidebar.classList.toggle('open'));
document.querySelectorAll('.sidebar nav a').forEach(a=>a.addEventListener('click',()=>document.querySelectorAll('.sidebar nav a').forEach(x=>x.classList.remove('active'))));
