import math
import scipy.stats as stat

def norm_cdf(x):
    return stat.norm.cdf(x)

def norm_pdf(x):
    return stat.norm.pdf(x)

def black_scholes_price(S, K, T, r, sigma, option_type):
    """
    S: Spot Price
    K: Strike Price
    T: Time to Expiry (in years)
    r: Risk-free rate (annualized)
    sigma: Implied Volatility (annualized)
    option_type: 'CE' or 'PE'
    """
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if option_type == 'CE' else max(0.0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == 'CE':
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def vega(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return S * norm_pdf(d1) * math.sqrt(T)

def implied_volatility(S, K, T, r, market_price, option_type, tol=1e-5, max_iterations=100):
    """
    Newton-Raphson method to find Implied Volatility
    """
    if T <= 0:
        return 0.0001
        
    # Intrinsic value check
    intrinsic = max(0.0, S - K) if option_type == 'CE' else max(0.0, K - S)
    if market_price <= intrinsic:
        return 0.0001

    sigma = 0.5 # initial guess
    
    for i in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        v = vega(S, K, T, r, sigma)
        
        diff = market_price - price
        
        if abs(diff) < tol:
            return sigma
            
        if v < 1e-6: # Avoid division by zero
            # Vega is too small, fallback to a smaller step or break
            return sigma
            
        sigma = sigma + diff / v
        # Ensure sigma doesn't go negative
        if sigma <= 0:
            sigma = 0.01

    return sigma

def compute_greeks(S, K, T, r, market_price, option_type):
    """
    Returns IV, Delta, Gamma, Theta, Vega
    """
    iv = implied_volatility(S, K, T, r, market_price, option_type)
    
    if T <= 0 or iv <= 0.0001:
        return {
            "iv": 0.0,
            "delta": 1.0 if option_type == 'CE' and S > K else (-1.0 if option_type == 'PE' and K > S else 0.0),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0
        }
        
    d1 = (math.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    
    v = vega(S, K, T, r, iv)
    gamma = norm_pdf(d1) / (S * iv * math.sqrt(T))
    
    if option_type == 'CE':
        delta = norm_cdf(d1)
        theta = (- (S * norm_pdf(d1) * iv) / (2 * math.sqrt(T)) 
                 - r * K * math.exp(-r * T) * norm_cdf(d2)) / 365.0
    else:
        delta = norm_cdf(d1) - 1
        theta = (- (S * norm_pdf(d1) * iv) / (2 * math.sqrt(T)) 
                 + r * K * math.exp(-r * T) * norm_cdf(-d2)) / 365.0
                 
    # V is annualized, usually Vega is expressed as price change per 1% change in IV
    v_percent = v / 100.0

    return {
        "iv": iv * 100.0, # as percentage
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": v_percent
    }
