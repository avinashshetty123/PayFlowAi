export const THEME_STORAGE_KEY = "payflow-theme";

/** Runs before first paint (inlined in app/layout.tsx) so a saved theme never flashes. */
export const THEME_BOOT_SCRIPT = `try{var t=localStorage.getItem("${THEME_STORAGE_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;
