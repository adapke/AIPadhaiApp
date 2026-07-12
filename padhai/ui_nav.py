"""Shared, persona-aware top navigation.

Before this, the module pages used two different, role-blind nav bars
(`ui_pages._page_shell` and `new_ui_pages._PAGE_PROLOGUE`), which is a big part
of why the app felt easy to get lost in. This is ONE bar, used by both shells,
whose primary links adapt to the signed-in user's role
(`localStorage.padhai_role`): student / teacher / parent / admin.

Design (thinking as each persona — what they actually need one tap away):
  - student: Home · Practice · Tutor · Flashcards · Progress
  - teacher: Dashboard · Classes · Practice · Tutor
  - parent:  Dashboard · Browse · Plans
  - admin:   School · Reports · Practice

The bar is self-contained (all classes prefixed `.phnav`) so it drops into
either the light (`ui_pages`) or dark (`new_ui_pages`) theme without clashing.
Everything is rendered client-side from the stored role, so the same static
markup works on every server-rendered page.
"""
from __future__ import annotations

NAV_STYLE = """
  .phnav{display:flex;align-items:center;gap:12px;height:54px;padding:0 16px;
    background:#101828;color:#eef3ff;flex-shrink:0;
    font-family:Inter,"Segoe UI",Arial,sans-serif}
  .phnav a{color:#aeb8cc;text-decoration:none}
  .phnav-brand{display:flex;align-items:center;gap:8px;font-weight:800;
    color:#fff;font-size:15px;white-space:nowrap}
  .phnav-logo{width:30px;height:30px;border-radius:8px;
    background:linear-gradient(135deg,#2f80ed,#12b76a);display:grid;
    place-items:center;color:#fff;font-weight:900;font-size:15px}
  .phnav-links{display:flex;align-items:center;gap:3px;flex:1;overflow-x:auto}
  .phnav-links a{padding:7px 11px;border-radius:7px;font-size:13px;
    font-weight:600;white-space:nowrap;color:#aeb8cc}
  .phnav-links a:hover{background:#1d2939;color:#fff;text-decoration:none}
  .phnav-links a.active{background:#1565d8;color:#fff}
  .phnav-role{background:#1d2939;color:#cfd8ea;border-radius:999px;
    padding:5px 11px;font-size:12px;white-space:nowrap;
    text-transform:capitalize;flex-shrink:0}
  .phnav-act{color:#aeb8cc;font-size:13px;border:1px solid #3d4f6e;
    border-radius:6px;padding:6px 10px;white-space:nowrap;flex-shrink:0}
  .phnav-act:hover{background:#1d2939;color:#fff;text-decoration:none}
  @media(max-width:640px){.phnav{gap:8px;padding:0 10px}
    .phnav-brand span:last-child{display:none}
    .phnav-role{display:none}}
"""

NAV_HTML = """
<nav class="phnav">
  <a class="phnav-brand" id="phnavBrand" href="/home">
    <span class="phnav-logo">P</span><span>AI Pathshala</span>
  </a>
  <div class="phnav-links" id="phnavLinks"></div>
  <span class="phnav-role" id="phnavRole"></span>
  <a class="phnav-act" href="/profile">Settings</a>
  <a class="phnav-act" href="#" id="phnavLogout">Sign out</a>
</nav>
"""

NAV_SCRIPT = """
(function(){
  var ROLE_NAV = {
    student: [['Home','/home'],['Practice','/practice'],['Tutor','/chat'],['Flashcards','/flashcards'],['Progress','/mastery']],
    teacher: [['Dashboard','/teacher'],['Classes','/school'],['Practice','/practice'],['Tutor','/chat']],
    parent:  [['Dashboard','/parent'],['Browse','/concept'],['Plans','/pricing']],
    admin:   [['School','/school'],['Reports','/dashboard'],['Practice','/practice']]
  };
  var HOME = {student:'/home',teacher:'/teacher',parent:'/parent',admin:'/school'};
  var here=(location.pathname||'/').replace(/\\/+$/,'')||'/';
  // Path-based role inference: the persona landing pages are unambiguous, so a
  // parent landing on /parent gets parent links even if no role was stored yet.
  // (/school is intentionally NOT here — it's shared by teacher + admin.)
  var PATH_ROLE = {'/parent':'parent','/teacher':'teacher'};
  var role;
  if(PATH_ROLE[here]){
    role=PATH_ROLE[here];
    try{localStorage.setItem('padhai_role',role);}catch(_){}
  } else {
    role=(localStorage.getItem('padhai_role')||'student').toLowerCase();
  }
  if(!ROLE_NAV[role]) role='student';
  var box=document.getElementById('phnavLinks');
  if(box){
    box.innerHTML=ROLE_NAV[role].map(function(l){
      var a=(here===l[1])?' class="active"':'';
      return '<a'+a+' href="'+l[1]+'">'+l[0]+'</a>';
    }).join('');
  }
  var b=document.getElementById('phnavBrand'); if(b) b.setAttribute('href',HOME[role]||'/home');
  var rc=document.getElementById('phnavRole'); if(rc) rc.textContent=role;
  var lo=document.getElementById('phnavLogout');
  if(lo) lo.addEventListener('click',function(e){
    e.preventDefault();
    try{localStorage.removeItem('pathshala_token');localStorage.removeItem('pathshala_email');}catch(_){}
    location.href='/landing';
  });
})();
"""
