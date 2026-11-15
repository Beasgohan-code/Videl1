'use client';
import Script from 'next/script';

/** The SDK supplies a raw signed string; only the backend may verify identity. */
export default function TelegramSdk(){
 return <Script id="telegram-miniapp-sdk" src="https://telegram.org/js/telegram-web-app.js" strategy="afterInteractive" onReady={()=>{window.dispatchEvent(new Event('videl:telegram-sdk-ready'))}}/>;
}
