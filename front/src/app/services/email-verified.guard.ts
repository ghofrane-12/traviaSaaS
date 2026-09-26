import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { Auth, authState } from '@angular/fire/auth';
import { UserService } from './user.service';
import { map, take } from 'rxjs/operators';

export const emailVerifiedGuard: CanActivateFn = () => {
  const auth    = inject(Auth);
  const userSvc = inject(UserService);
  const router  = inject(Router);

  if (!userSvc.isLoggedIn()) {
    return router.createUrlTree(['/login']);
  }

  const session      = userSvc.session();
  const firebaseUser = auth.currentUser;

  if (session?.role === 'visitor' || firebaseUser?.isAnonymous || !firebaseUser) {
    return true;
  }

  if (!firebaseUser.emailVerified) {
    const isPlaceholder  = firebaseUser.email?.endsWith('@placeholder.invalid') ?? false;
    const isFacebook     = firebaseUser.providerData.some(p => p.providerId === 'facebook.com');
    const isSocialNoEmail = firebaseUser.providerData.some(p =>
      ['facebook.com', 'google.com'].includes(p.providerId)
    ) && !firebaseUser.email;

    if (isPlaceholder || isFacebook || isSocialNoEmail) {
      return true;
    }
    return router.createUrlTree(['/verify-email']);
  }

  return true;
};