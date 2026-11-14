(() => {
  const moduleUrl = new URL('./design-3d.js', document.currentScript.src).href;
  import(moduleUrl).catch(error => {
    console.warn('Three.js preview module could not load; the built-in product preview remains available.', error);
    window.dispatchEvent(new CustomEvent('ansuman-three-failed', { detail: error }));
  });
})();
