// H49/G6 · el idioma de las páginas que sirve mpvd (la sala, el mando, el panel de descargas).
//
// Lo sirven los dos servidores en /i18n.js (la sala, en /static/i18n.js) poniendo delante el catálogo de ESA
// página en el idioma que pide el navegador de quien la abre:  window.MU_T = { "<castellano>": "<traducción>" }.
// Vacío = castellano, que es como está escrita la página.
//
// Va en un fichero aparte y no dentro del HTML porque la sala se sirve con `script-src 'self'`: un <script> en
// línea lo bloquea el navegador sin decir nada (ni salta window.onerror), y la página se queda muda.
//
// `t('…')` busca la cadena y sustituye cada %s por los argumentos, en orden. Lo que ya viene escrito en el HTML
// se traduce aquí mismo, antes de que se vea: el texto de cada etiqueta y los atributos que una persona lee.
function t(s) {
  var a = arguments, i = 1, v = (window.MU_T || {})[s] || s;
  return v.replace(/%s/g, function () { return a[i++]; });
}
(function () {
  var cat = window.MU_T || {};
  if (!Object.keys(cat).length) return;
  if (cat[document.title]) document.title = cat[document.title];
  var w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false), nodos = [], n;
  while ((n = w.nextNode())) nodos.push(n);
  nodos.forEach(function (nodo) {
    var padre = nodo.parentNode.nodeName;
    if (padre === 'SCRIPT' || padre === 'STYLE') return;
    var m = /^(\s*)([\s\S]*?)(\s*)$/.exec(nodo.nodeValue), clave = m[2].replace(/\s+/g, ' ');
    if (clave && cat[clave]) nodo.nodeValue = m[1] + cat[clave] + m[3];
  });
  ['placeholder', 'title', 'aria-label', 'alt'].forEach(function (at) {
    Array.prototype.forEach.call(document.querySelectorAll('[' + at + ']'), function (el) {
      var v = el.getAttribute(at).replace(/\s+/g, ' ').trim();
      if (cat[v]) el.setAttribute(at, cat[v]);
    });
  });
})();
