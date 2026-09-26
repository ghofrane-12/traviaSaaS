// services/price.service.ts
import { Injectable } from '@angular/core';

export interface PriceDisplay {
  amount: number;
  currency: string;
  formatted: string;
  hasConversion?: boolean;
  originalCurrency?: string;
  originalAmount?: number;
}

@Injectable({ providedIn: 'root' })
export class PriceService {

  private readonly SYMBOLS: Record<string, string> = {
    EUR: '€', USD: '$', GBP: '£', TND: 'DT',
    AED: 'د.إ', MAD: 'DH', DZD: 'DA', CHF: 'CHF',
    CAD: 'CA$', JPY: '¥', QAR: 'QR', SAR: 'SR',
    KWD: 'KD', TRY: '₺',
  };

  format(amount: number | null | undefined, currency: string, decimals = 0): string {
    if (amount == null || isNaN(amount) || amount <= 0) return '–';
    const sym = this.SYMBOLS[currency?.toUpperCase()] ?? currency;
    const formatted = new Intl.NumberFormat('fr-FR', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(amount);
    const after = ['TND', 'MAD', 'DZD', 'AED', 'QAR', 'SAR', 'KWD'].includes(currency?.toUpperCase());
    return after ? `${formatted} ${sym}` : `${sym}${formatted}`;
  }


  getPriceDisplay(offer: any, field = 'price'): PriceDisplay {
    const amount   = offer?.[field] ?? 0;
    const currency = (offer?.display_currency ?? offer?.currency ?? 'EUR').toUpperCase();
    
    const originalCurrency = offer?.original_currency?.toUpperCase();
    const hasConversion = !!originalCurrency && originalCurrency !== currency;

    return {
      amount,
      currency,
      formatted: this.format(amount, currency),
      hasConversion,
      originalCurrency: hasConversion ? originalCurrency : undefined,
      originalAmount:   hasConversion ? (offer?.original_amount ?? offer?.original_price ?? null) : undefined,
    };
  }


getHotelPrices(hotel: any) {
  const currency = (
    hotel?.display_currency ?? 
    hotel?.target_currency  ?? 
    hotel?.currency         ?? 
    'EUR'
  ).toUpperCase();

  const originalCurrency = hotel?.original_currency?.toUpperCase();
  const hasConversion = !!originalCurrency && originalCurrency !== currency;

  const perNightAmount = hotel?.price_per_night_converted ?? hotel?.price_per_night ?? 0;
  const totalAmount    = hotel?.total_price_converted     ?? hotel?.total_price     ?? 0;
  const rangeMin       = hotel?.price_range?.min ?? null;
  const rangeMax       = hotel?.price_range?.max ?? null;

  const perNight: PriceDisplay = {
    amount:           perNightAmount,
    currency,
    formatted:        this.format(perNightAmount, currency),
    hasConversion,
    originalCurrency: hasConversion ? originalCurrency : undefined,
    originalAmount:   hasConversion ? (hotel?.price_per_night_original ?? null) : undefined,
  };

  const total: PriceDisplay = {
    amount:           totalAmount,
    currency,
    formatted:        this.format(totalAmount, currency),
    hasConversion,
    originalCurrency: hasConversion ? originalCurrency : undefined,
    originalAmount:   hasConversion ? (hotel?.total_price_original ?? null) : undefined,
  };

  return {
    perNight,
    total,
    rangeMin : this.format(rangeMin, currency),
    rangeMax : this.format(rangeMax, currency),
    currency,
    nights   : hotel?.nights ?? null,
  };
}
  getRoomOfferPrices(offer: any) {
    const currency = (offer?.display_currency ?? offer?.currency ?? 'EUR').toUpperCase();
    return {
      total    : this.getPriceDisplay(offer, 'price'),
      perNight : this.getPriceDisplay(offer, 'price_per_night'),
      currency,
    };
  }


  getFlightPrices(flight: any, fareOption?: any) {
    const src      = fareOption ?? flight;
    const currency = (src?.display_currency ?? src?.currency ?? flight?.currency ?? 'EUR').toUpperCase();
    return {
      base: this.getPriceDisplay(src, 'price'),
      currency,
    };
  }


  getActivityPrices(activity: any) {
    const currency = (activity?.display_currency ?? activity?.currency ?? 'EUR').toUpperCase();
    return {
      from     : this.getPriceDisplay(activity, 'price'),
      rangeMin : this.format(activity?.price_min, currency),
      rangeMax : this.format(activity?.price_max, currency),
      currency,
    };
  }

  getTransportPrices(transport: any) {
    return {
      base    : this.getPriceDisplay(transport, 'price'),
      currency: (transport?.display_currency ?? transport?.currency ?? 'EUR').toUpperCase(),
    };
  }

 getRestaurantPrices(item: any) {
  const currency = (item?.display_currency ?? item?.currency ?? 'EUR').toUpperCase();
  return {
    price    : this.getPriceDisplay(item, 'price'),
    rangeMin : this.format(item?.price_range?.min ?? null, currency),
    rangeMax : this.format(item?.price_range?.max ?? null, currency),
    currency,
  };
}

  getSuggestionPrice(suggestion: any): string {
    if (!suggestion?.price || suggestion.price === 0) return '';
    return this.format(suggestion.price, suggestion.currency ?? 'EUR');
  }
}