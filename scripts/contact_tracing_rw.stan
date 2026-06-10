// Bayesian random-walk model for daily contact-tracing follow-up rates
// MVE DRC 2026 — INSP SitRep Tableau 3 data
//
// Model:
//   followed[t] ~ Binomial(listed[t], p_t[t])
//   logit(p_t[t]) = mu[t]
//   mu[t] | mu[t-1] ~ Normal(mu[t-1], sigma)   (random walk)
//   mu[1] ~ Normal(0, 1.5)
//   sigma ~ Half-Normal(0, 0.5)
//
// The random walk is reparameterised in a non-centred form for efficiency.

data {
  int<lower=1>           T;
  array[T] int<lower=0>  followed;
  array[T] int<lower=0>  listed;
}

parameters {
  real                   mu1;          // logit follow-up rate at t = 1
  vector[T - 1]          z;            // non-centred increments
  real<lower=0>          sigma;        // random-walk SD (logit scale)
}

transformed parameters {
  vector[T] mu;
  mu[1] = mu1;
  for (t in 2:T)
    mu[t] = mu[t - 1] + sigma * z[t - 1];
}

model {
  // Priors
  mu1   ~ normal(0, 1.5);
  z     ~ std_normal();               // non-centred → N(0,1) increments
  sigma ~ normal(0, 0.5);

  // Likelihood
  for (t in 1:T)
    followed[t] ~ binomial_logit(listed[t], mu[t]);
}

generated quantities {
  vector[T]              p_t;          // follow-up probability at each time
  array[T] int           followed_rep; // posterior predictive draws

  for (t in 1:T) {
    p_t[t]          = inv_logit(mu[t]);
    followed_rep[t] = binomial_rng(listed[t], p_t[t]);
  }
}
