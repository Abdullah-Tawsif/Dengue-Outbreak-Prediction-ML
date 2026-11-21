
const form=document.getElementById('registerForm'),pw=document.getElementById('password'),confirm=document.getElementById('confirmPassword'),bar=document.getElementById('meterBar'),match=document.getElementById('matchStatus'),status=document.getElementById('formStatus');
document.querySelectorAll('.eye-btn').forEach(btn=>btn.addEventListener('click',()=>{const input=document.getElementById(btn.dataset.target);input.type=input.type==='password'?'text':'password'}));
pw.addEventListener('input',()=>{let score=0;if(pw.value.length>=8)score++;if(/[A-Z]/.test(pw.value))score++;if(/[0-9]/.test(pw.value))score++;if(/[^A-Za-z0-9]/.test(pw.value))score++;bar.style.width=(score*25)+'%';bar.style.background=score<2?'#9e1b32':score<4?'#c56b78':'#7eb78f'});
function checkMatch(){if(!confirm.value){match.textContent='';return}if(pw.value===confirm.value){match.textContent='✓ Passwords match';match.style.color='#8fc59d'}else{match.textContent='Passwords do not match';match.style.color='#e88998'}}
confirm.addEventListener('input',checkMatch);pw.addEventListener('input',checkMatch);
form.addEventListener('submit',e=>{e.preventDefault();if(!form.checkValidity()||pw.value!==confirm.value){status.textContent='Please complete the form correctly.';status.style.color='#e88998';return}status.textContent='Demo registration validated successfully.';status.style.color='#8fc59d'});
