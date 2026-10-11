(async function mountSystemSidebar(){
  const escapeHtml=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  try{
    const [userResponse,navigationResponse]=await Promise.all([fetch('/api/auth/me'),fetch('/api/system/navigation')]);
    if(userResponse.status===401){location.href='/login';return}
    if(!userResponse.ok||!navigationResponse.ok)return;
    const user=await userResponse.json(),navigation=await navigationResponse.json();
    const aside=document.createElement('aside');
    aside.className='system-side';
    aside.setAttribute('aria-label','系统导航');
    aside.innerHTML=`<div class="system-brand"><div class="system-brand-mark">EEG</div><div><strong>脑电智控平台</strong><small>Neural Control Center</small></div></div><nav class="system-nav">${navigation.items.map(item=>`<a href="${escapeHtml(item.path)}" class="${location.pathname===item.path?'active':''}"><span class="system-nav-icon">${escapeHtml(item.icon)}</span>${escapeHtml(item.title)}</a>`).join('')}</nav><div class="system-user-wrap"><div class="system-user"><strong>${escapeHtml(user.display_name)}</strong><small>${escapeHtml(user.role_description)}</small></div><button class="system-logout" type="button">退出登录</button></div>`;
    aside.querySelector('.system-logout').addEventListener('click',async()=>{await fetch('/api/auth/logout',{method:'POST'});location.href='/login'});
    document.body.prepend(aside);
    document.body.classList.add('has-system-sidebar');
  }catch(error){console.warn('系统导航加载失败',error)}
})();
