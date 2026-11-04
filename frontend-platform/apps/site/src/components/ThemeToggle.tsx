import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
export default function ThemeToggle() {
  const [light, setLight] = useState(false);
  useEffect(() => { setLight(document.documentElement.dataset.theme === 'light'); }, []);
  const toggle = () => {
    const next = !light;
    document.documentElement.dataset.theme = next ? 'light' : 'dark';
    try { localStorage.setItem('videl_public_theme', next ? 'light' : 'dark'); } catch { /* optional preference */ }
    setLight(next);
  };
  return <button className="theme-button" type="button" aria-label={light ? 'Switch to dark theme' : 'Switch to light theme'} onClick={toggle}>{light ? <Moon size={17} aria-hidden="true"/> : <Sun size={17} aria-hidden="true"/>}</button>;
}
