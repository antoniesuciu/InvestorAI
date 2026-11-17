import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { createQueryClient } from './api';
import App from './App';
import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource-variable/source-serif-4/opsz.css';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode><QueryClientProvider client={createQueryClient()}><App /></QueryClientProvider></StrictMode>,
);
