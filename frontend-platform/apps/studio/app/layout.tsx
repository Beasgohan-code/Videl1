import type {Metadata} from 'next';
import './globals.css';
import TelegramSdk from '../components/TelegramSdk';
export const metadata:Metadata={title:'Videl Studio · Migration preview',description:'An in-progress workspace interface for Videl.'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en" data-theme="dark"><body><TelegramSdk/>{children}</body></html>}
