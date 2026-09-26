import { HttpInterceptorFn, HttpRequest, HttpHandlerFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { from, switchMap } from 'rxjs';
import { UserService } from '../services/user.service';

export const authInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn
) => {
  const userService = inject(UserService);

  if (!req.url.includes('/api/')) {
  return next(req);
}
  return from(userService.getFreshIdToken()).pipe(
    switchMap(token => {
      // ✅ Toujours envoyer le tenant_id, même pour les visiteurs
      const tenantId = userService.resolveTenantId();

      const headers: Record<string, string> = {
        'X-Tenant-ID': tenantId
      };

      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const cloned = req.clone({ setHeaders: headers });
      return next(cloned);
    })
  );
};