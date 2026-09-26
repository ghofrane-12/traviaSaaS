import { Routes } from '@angular/router';
import { authGuard }          from './services/authe.guard';
import { emailVerifiedGuard } from './services/email-verified.guard'; // ← ajuste le chemin
import { LoginComponent }     from './components/login/login.component';
import { RegisterComponent }  from './components/register/register.component';
import { VerifyEmailComponent } from './components/verify-email/verify-email.component';

export const routes: Routes = [

  { path: 'login',        component: LoginComponent },
  { path: 'register',     component: RegisterComponent },
  { path: 'verify-email', component: VerifyEmailComponent },

  {
    path: 'search',
    loadComponent: () =>
      import('./components/search/search.component').then(m => m.SearchComponent)
  },

  {
    path: 'chat',
    canActivate: [authGuard, emailVerifiedGuard],
    loadComponent: () => import('./components/main-page/main-page.component')
      .then(m => m.MainPageComponent)
  },
  {
    path: 'config',
    canActivate: [authGuard, emailVerifiedGuard],
    data: { roles: ['client', 'admin', 'super_admin'] },
    loadComponent: () => import('./components/pages/admin-config/admin-config.component')
      .then(m => m.AdminConfigComponent)
  },
  {
    path: 'results',
    canActivate: [authGuard, emailVerifiedGuard],
    loadComponent: () => import('./components/results-page/results-page.component')
      .then(m => m.ResultsPageComponent)
  },
  {
    path: 'flight/:id',
    canActivate: [authGuard, emailVerifiedGuard],
    loadComponent: () => import('./components/flight-details/flight-details.component')
      .then(m => m.FlightDetailsComponent)
  },
  {
    path: 'hotel/:id',
    canActivate: [authGuard, emailVerifiedGuard],
    loadComponent: () => import('./components/hotel-details/hotel-details.component')
      .then(m => m.HotelDetailsComponent)
  },
  {
    path: 'activity/:id',
    canActivate: [authGuard, emailVerifiedGuard],
    loadComponent: () => import('./components/activity-details/activity-details.component')
      .then(m => m.ActivityDetailsComponent)
  },
  {
    path: 'super-admin',
    canActivate: [authGuard, emailVerifiedGuard],
    data: { roles: ['super_admin'] },
    loadComponent: () => import('./components/pages/super-admin/super-admin.component')
      .then(m => m.SuperAdminComponent)
  },
  {
  path: 'history',
  canActivate: [authGuard, emailVerifiedGuard],
  loadComponent: () => import('./components/history/history.component')
    .then(m => m.HistoryComponent)
  },

  { path: '',   redirectTo: 'chat', pathMatch: 'full' },
  { path: '**', redirectTo: 'chat' },
];