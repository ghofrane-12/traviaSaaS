import { inject } from '@angular/core';
import { CanActivateFn, Router, ActivatedRouteSnapshot } from '@angular/router';
import { UserService } from '../services/user.service';

export const authGuard: CanActivateFn = (route: ActivatedRouteSnapshot) => {
  const userService = inject(UserService);
  const router      = inject(Router);

  const tenantFromUrl = route.queryParams['tenant'];
  if (tenantFromUrl) {
    sessionStorage.setItem('tenant_id', tenantFromUrl);
  }

  if (!userService.isLoggedIn()) {
    const url = route.url.toString();
    if (url && url !== 'verify-email') {
      sessionStorage.setItem('redirectAfterLogin', url);
    }
    return router.createUrlTree(['/login']);
  }

  const requiredRoles = route.data?.['roles'] as string[] | undefined;
  if (requiredRoles && !userService.hasRole(...requiredRoles as any)) {
    return router.createUrlTree(['/unauthorized']);
  }

  return true;
};