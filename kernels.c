/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#if defined(_WIN32)
#define API __declspec(dllexport)
#else
#define API __attribute__((visibility("default")))
#endif
#define EPS 1e-10
API int nwh_abi(void) { return 1; }
static double limit(double x,double a,double b) { return fmin(b,fmax(a,x)); }
static double dot(const double *a,const double *b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
static double norm(const double *a) { return sqrt(dot(a,a)); }
static void cross(const double *a,const double *b,double *r) {
    r[0]=a[1]*b[2]-a[2]*b[1];r[1]=a[2]*b[0]-a[0]*b[2];r[2]=a[0]*b[1]-a[1]*b[0];
}
static intptr_t right(const double *a,intptr_t n,double value) {
    intptr_t lo=0,hi=n;
    while(lo<hi) { intptr_t m=lo+(hi-lo)/2;if(value<a[m])hi=m;else lo=m+1; }
    return lo;
}
static intptr_t left(const double *a,intptr_t n,double value) {
    intptr_t lo=0,hi=n;
    while(lo<hi) { intptr_t m=lo+(hi-lo)/2;if(a[m]<value)lo=m+1;else hi=m; }
    return lo;
}
static void curve(const double *arc,const double *parameters,intptr_t count,const double *coefs,intptr_t segments,double total,double u,double *out) {
    double x=limit(u,0.,1.)*total,p;
    intptr_t k=right(arc,count,x)-1;
    if(k<0)p=parameters[0];
    else if(k>=count-1)p=parameters[count-1];
    else p=parameters[k]+(x-arc[k])/(arc[k+1]-arc[k])*(parameters[k+1]-parameters[k]);
    if(!isfinite(p)) { for(int d=0;d<3;d++)out[d]=NAN;return; }
    intptr_t i=(intptr_t)p;if(i<0)i=0;if(i>=segments)i=segments-1;
    double t=p-i,tt=t*t,ttt=tt*t;
    const double *c=coefs+12*i;
    for(int d=0;d<3;d++)out[d]=(2*ttt-3*tt+1)*c[d]+(-2*ttt+3*tt)*c[3+d]+(ttt-2*tt+t)*c[6+d]+(ttt-tt)*c[9+d];
}
API int nwh_projection(const double *arc,const double *parameters,intptr_t count,const double *coefs,intptr_t segments,double total,const double *u,const double *a,const double *ta,intptr_t n,double *v) {
    if(n<3||count<2||segments<1)return -1;
    for(intptr_t i=0;i<n;i++)v[i]=u[i];
    for(int iteration=0;iteration<8;iteration++) {
        for(intptr_t i=0;i<n;i++) {
            double b[3],hi[3],lo[3],derivative[3],tangent[3],delta[3];
            curve(arc,parameters,count,coefs,segments,total,v[i],b);
            curve(arc,parameters,count,coefs,segments,total,fmin(1.,v[i]+1e-4),hi);
            curve(arc,parameters,count,coefs,segments,total,fmax(0.,v[i]-1e-4),lo);
            for(int d=0;d<3;d++)derivative[d]=(hi[d]-lo[d])/2e-4;
            double size=fmax(EPS,norm(derivative));
            for(int d=0;d<3;d++)tangent[d]=ta[3*i+d]+derivative[d]/size;
            size=fmax(EPS,norm(tangent));
            for(int d=0;d<3;d++) { tangent[d]/=size;delta[d]=b[d]-a[3*i+d]; }
            double step=-dot(delta,tangent)/fmax(EPS,dot(derivative,tangent));
            v[i]=limit(v[i]+.7*limit(step,-.08,.08),0.,1.);
        }
        v[0]=0.;v[n-1]=1.;
        for(intptr_t i=1;i<n-1;i++)v[i]=fmax(v[i],v[i-1]+1e-6);
        for(intptr_t i=n-2;i>0;i--)v[i]=fmin(v[i],v[i+1]-1e-6);
    }
    double error=0.;
    for(intptr_t i=0;i<n;i++) {
        if(i&&v[i]-v[i-1]<1e-5)return 0;
        if(!i||i==n-1)continue;
        double b[3],hi[3],lo[3],derivative[3],tangent[3],delta[3];
        curve(arc,parameters,count,coefs,segments,total,v[i],b);
        curve(arc,parameters,count,coefs,segments,total,fmin(1.,v[i]+1e-4),hi);
        curve(arc,parameters,count,coefs,segments,total,fmax(0.,v[i]-1e-4),lo);
        for(int d=0;d<3;d++)derivative[d]=(hi[d]-lo[d])/2e-4;
        double size=fmax(EPS,norm(derivative));
        for(int d=0;d<3;d++) { tangent[d]=ta[3*i+d]+derivative[d]/size;delta[d]=b[d]-a[3*i+d]; }
        double cosine=fabs(dot(delta,tangent))/fmax(EPS,norm(delta)*norm(tangent));
        error+=cosine*cosine;
    }
    return !isfinite(error)||error/(n-2)>.18?0:1;
}
API intptr_t nwh_zipper(const double *a,intptr_t na,const double *b,intptr_t nb,intptr_t *out) {
    if(na<nb||nb<2)return -1;
    intptr_t extra=na-nb,width=extra+1;
    if(nb>400000/width)return -1;
    double *before=malloc(sizeof(double)*width),*current=malloc(sizeof(double)*width);
    uint8_t *steps=malloc((size_t)nb*width);
    if(!before||!current||!steps) { free(before);free(current);free(steps);return -1; }
    for(intptr_t k=0;k<width;k++)before[k]=INFINITY;
    for(intptr_t j=0;j<nb;j++) {
        for(intptr_t k=0;k<width;k++) {
            if(!j&&!k) { current[k]=0.;continue; }
            double dx=a[j+k]-b[j],error=dx*dx;
            double diagonal=j?before[k]:INFINITY,triangle=k?current[k-1]:INFINITY;
            if(diagonal<=triangle) { current[k]=diagonal+error;steps[j*width+k]=1; }
            else { current[k]=triangle+error;steps[j*width+k]=2; }
        }
        double *swap=before;before=current;current=swap;
    }
    intptr_t j=nb-1,k=extra,n=0;
    out[0]=j+k;out[1]=j;n++;
    while(j||k) {
        if(steps[j*width+k]==1)j--;else k--;
        out[2*n]=j+k;out[2*n+1]=j;n++;
    }
    free(before);free(current);free(steps);return n;
}
static void tangents(const double *points,intptr_t n,double *out) {
    for(intptr_t i=0;i<n;i++) {
        const double *a=points+3*(i?i-1:0),*b=points+3*(i+1<n?i+1:n-1);
        double *t=out+3*i;for(int d=0;d<3;d++)t[d]=b[d]-a[d];
        double size=norm(t);for(int d=0;d<3;d++)t[d]=size>EPS?t[d]/size:0.;
    }
}
static double skew(const double *a,const double *b,const double *ta,const double *tb) {
    double delta[3],tangent[3];for(int d=0;d<3;d++) { delta[d]=b[d]-a[d];tangent[d]=ta[d]+tb[d]; }
    double na=norm(delta),nt=norm(tangent);return na>EPS&&nt>EPS?fabs(dot(delta,tangent))/(na*nt):0.;
}
static int folded(const double *a0,const double *a1,const double *b0,const double *b1) {
    double e[4][3],diagonal[3],one[3],two[3],normal[3];
    for(int d=0;d<3;d++) { e[0][d]=b0[d]-a0[d];e[1][d]=b1[d]-b0[d];e[2][d]=a1[d]-b1[d];e[3][d]=a0[d]-a1[d];diagonal[d]=b1[d]-a0[d]; }
    cross(e[0],diagonal,one);double end[3];for(int d=0;d<3;d++)end[d]=a1[d]-a0[d];cross(diagonal,end,two);
    double na=norm(one),nb=norm(two);
    if(na>0.&&nb>0.&&dot(one,two)<.25*na*nb)return 1;
    for(int d=0;d<3;d++)normal[d]=one[d]+two[d];double area=norm(normal);
    for(int i=0;i<4;i++) { double product[3];cross(e[i],e[(i+1)%4],product);double signed_=dot(product,normal);if(signed_<0.&&signed_<-.015*norm(e[i])*norm(e[(i+1)%4])*area)return 1; }
    return 0;
}
API intptr_t nwh_geometry(const double *a,intptr_t na,const double *b,intptr_t nb,const double *fa,const double *fb,const intptr_t *baseline,intptr_t base_count,intptr_t *out) {
    if(na<2||nb<2||na>160000/nb)return -1;
    double *ta=malloc(sizeof(double)*3*na),*tb=malloc(sizeof(double)*3*nb);
    intptr_t *bounds=malloc(sizeof(intptr_t)*2*na);
    double *before=malloc(sizeof(double)*nb),*current=malloc(sizeof(double)*nb);
    uint8_t *steps=calloc((size_t)na*nb,1);
    intptr_t answer=-1;
    if(!ta||!tb||!bounds||!before||!current||!steps)goto cleanup;
    tangents(a,na,ta);tangents(b,nb,tb);int severe=0;
    for(intptr_t k=0;k<base_count;k++) {
        intptr_t i=baseline[2*k],j=baseline[2*k+1];
        if(i<0||i>=na||j<0||j>=nb)goto cleanup;
        if(skew(a+3*i,b+3*j,ta+3*i,tb+3*j)>.65)severe=1;
        if(k+1<base_count) { intptr_t ni=baseline[2*k+2],nj=baseline[2*k+3];if(ni<0||ni>=na||nj<0||nj>=nb)goto cleanup;if(ni>i&&nj>j&&folded(a+3*i,a+3*ni,b+3*j,b+3*nj))severe=1; }
    }
    if(!severe) { answer=0;goto cleanup; }
    for(intptr_t i=0;i<na;i++) { bounds[2*i]=nb;bounds[2*i+1]=-1; }
    for(intptr_t k=0;k<base_count;k++) { intptr_t i=baseline[2*k],j=baseline[2*k+1];if(j<bounds[2*i])bounds[2*i]=j;if(j>bounds[2*i+1])bounds[2*i+1]=j; }
    for(intptr_t j=0;j<nb;j++)before[j]=INFINITY;
    for(intptr_t i=0;i<na;i++) {
        intptr_t center=left(fb,nb,fa[i]);if(center>=nb)center=nb-1;
        intptr_t lo=bounds[2*i]<center?bounds[2*i]:center,hi=bounds[2*i+1]>center?bounds[2*i+1]:center;
        lo=lo>2?lo-2:0;hi=hi+3<nb?hi+3:nb;
        for(intptr_t j=0;j<nb;j++)current[j]=INFINITY;
        for(intptr_t j=lo;j<hi;j++) {
            if(!i&&!j) { current[j]=0.;continue; }
            double angle=skew(a+3*i,b+3*j,ta+3*i,tb+3*j),dx=fa[i]-fb[j];
            double value=angle*angle+.1*dx*dx,penalty=i&&j&&folded(a+3*(i-1),a+3*i,b+3*(j-1),b+3*j)?5.:0.;
            double best=i&&j?before[j-1]+value+penalty:INFINITY;uint8_t step=1;
            if(i) { double trial=before[j]+value+.22;if(trial<best) { best=trial;step=2; } }
            if(j) { double trial=current[j-1]+value+.22;if(trial<best) { best=trial;step=3; } }
            current[j]=best;steps[i*nb+j]=step;
        }
        double *swap=before;before=current;current=swap;
    }
    if(!isfinite(before[nb-1])) { answer=0;goto cleanup; }
    intptr_t i=na-1,j=nb-1,n=0;out[0]=i;out[1]=j;n++;
    while(i||j) { uint8_t step=steps[i*nb+j];if(step==1||step==2)i--;if(step==1||step==3)j--;if(!step)goto cleanup;out[2*n]=i;out[2*n+1]=j;n++; }
    answer=n;
cleanup:
    free(ta);free(tb);free(bounds);free(before);free(current);free(steps);return answer;
}
static void rotate(const double *value,const double *old,const double *tangent,double *out) {
    double axis[3];cross(old,tangent,axis);double sine=norm(axis);
    if(sine<EPS) { for(int d=0;d<3;d++)out[d]=value[d];return; }
    for(int d=0;d<3;d++)axis[d]/=sine;
    double cosine=limit(dot(old,tangent),-1.,1.),product[3];cross(axis,value,product);
    double along=dot(axis,value)*(1.-cosine);
    for(int d=0;d<3;d++)out[d]=(value[d]*cosine+product[d]*sine)+axis[d]*along;
}
API int nwh_normals(const double *a,const double *b,intptr_t n,const double *reference,double *out) {
    if(n<1)return -1;
    double previous[3]={0},old[3]={0},sign=1.;
    for(intptr_t i=0;i<n;i++) {
        intptr_t l=i?i-1:0,r=i+1<n?i+1:n-1;
        double tangent[3],width[3],raw[3],transported[3];
        for(int d=0;d<3;d++) { tangent[d]=(a[3*r+d]+b[3*r+d])*.5-(a[3*l+d]+b[3*l+d])*.5;width[d]=b[3*i+d]-a[3*i+d]; }
        double size=norm(tangent);for(int d=0;d<3;d++)tangent[d]=size>EPS?tangent[d]/size:0.;
        cross(width,tangent,raw);size=norm(raw);
        if(size>EPS)for(int d=0;d<3;d++)raw[d]/=size;
        else if(i)rotate(previous,old,tangent,raw);
        else for(int d=0;d<3;d++)raw[d]=reference[d];
        if(!i)sign=dot(raw,reference)<0.?-1.:1.;
        else { rotate(previous,old,tangent,transported);if(dot(raw,transported)<0.)sign=-sign; }
        for(int d=0;d<3;d++) { out[3*i+d]=sign*raw[d];previous[d]=raw[d];old[d]=tangent[d]; }
    }
    return 1;
}
API int nwh_grid(const double *a,const double *b,const double *normals,intptr_t n,const double *profile,const double *sine,intptr_t w,double bulge,const double *weights,const double *shifts,double *out) {
    if(n<1||w<2)return -1;
    for(intptr_t i=0;i<n;i++) {
        double delta[3];for(int d=0;d<3;d++)delta[d]=b[3*i+d]-a[3*i+d];double width=norm(delta);
        for(intptr_t x=0;x<w;x++) {
            double v=profile[x],amount=bulge*width*sine[x];
            for(int d=0;d<3;d++)out[(i*w+x)*3+d]=(a[3*i+d]*(1.-v)+b[3*i+d]*v)+normals[3*i+d]*amount;
            if(x&&x<w-1)for(int end=0;end<2;end++)for(int d=0;d<3;d++)out[(i*w+x)*3+d]+=weights[2*i+end]*shifts[x*6+end*3+d];
        }
    }
    return 1;
}
static int compare_id(const void *a,const void *b) {
    intptr_t x=*(const intptr_t *)a,y=*(const intptr_t *)b;return (x>y)-(x<y);
}
API int nwh_assembly(const double *points,intptr_t nv,const intptr_t *flat,intptr_t nfvalues,const intptr_t *offsets,intptr_t nf,uint8_t *keep,intptr_t *remap,intptr_t *used,intptr_t *indices,intptr_t *out_offsets,intptr_t *next,intptr_t *face_ids,intptr_t *counts) {
    if(nv<0||nf<0||nf>1000000||offsets[0]!=0||offsets[nf]!=nfvalues)return -1;
    size_t capacity=1;while(capacity<(size_t)nf*2+1)capacity*=2;
    intptr_t *table=calloc(capacity,sizeof(intptr_t)),*sorted=malloc((size_t)(nfvalues+1)*sizeof(intptr_t));
    if(!table||!sorted) { free(table);free(sorted);return -1; }
    for(intptr_t v=0;v<nv;v++)remap[v]=-1;
    intptr_t status=1,face_count=0,vertex_count=0,value_count=0;out_offsets[0]=0;
    for(intptr_t f=0;f<nf;f++) {
        keep[f]=0;intptr_t begin=offsets[f],end=offsets[f+1],n=end-begin;
        if(begin<0||end>nfvalues||n<3) { status=-1;goto assembly_end; }
        double area[3]={0.,0.,0.},scale=0.;
        for(intptr_t j=begin;j<end;j++) {
            intptr_t x=flat[j],y=flat[j+1<end?j+1:begin];
            if(x<0||x>=nv||y<0||y>=nv) { status=-1;goto assembly_end; }
            const double *a=points+3*x,*b=points+3*y;double delta[3];
            for(int d=0;d<3;d++) { int e=(d+1)%3,k=(d+2)%3;area[d]+=(a[e]-b[e])*(a[k]+b[k]);delta[d]=a[d]-b[d]; }
            scale=fmax(scale,dot(delta,delta));sorted[j]=x;
        }
        double tolerance=EPS*scale;if(!(dot(area,area)>tolerance*tolerance))continue;
        qsort(sorted+begin,(size_t)n,sizeof(intptr_t),compare_id);
        uint64_t hash=UINT64_C(14695981039346656037)^(uint64_t)n;
        for(intptr_t j=begin;j<end;j++) { hash^=(uint64_t)sorted[j];hash*=UINT64_C(1099511628211); }
        size_t slot=(size_t)hash&(capacity-1);int duplicate=0;
        while(table[slot]) {
            intptr_t other=table[slot]-1,start=offsets[other];
            if(offsets[other+1]-start==n&&!memcmp(sorted+begin,sorted+start,(size_t)n*sizeof(intptr_t))) { duplicate=1;break; }
            slot=(slot+1)&(capacity-1);
        }
        if(duplicate)continue;table[slot]=f+1;keep[f]=1;face_count++;
        for(intptr_t j=begin;j<end;j++)remap[flat[j]]=-2;
    }
    for(intptr_t v=0;v<nv;v++)if(remap[v]==-2) { remap[v]=vertex_count;used[vertex_count++]=v; }
    intptr_t f_out=0;
    for(intptr_t f=0;f<nf;f++)if(keep[f]) {
        for(intptr_t j=offsets[f];j<offsets[f+1];j++) {
            indices[value_count]=remap[flat[j]];next[value_count]=remap[flat[j+1<offsets[f+1]?j+1:offsets[f]]];face_ids[value_count]=f_out;value_count++;
        }
        out_offsets[++f_out]=value_count;
    }
    counts[0]=face_count;counts[1]=vertex_count;counts[2]=value_count;
assembly_end:
    free(table);free(sorted);return (int)status;
}
API int nwh_shade(const double *points,intptr_t nv,const intptr_t *offsets,intptr_t nf,const intptr_t *vertices,const intptr_t *next,const intptr_t *groups,intptr_t nc,int smooth,double *out) {
    if(nc<0||nf<0||nf>nc||(!nf&&nc))return -1;
    double *sums=smooth?calloc((size_t)(nc+1)*3,sizeof(double)):NULL;
    if(smooth&&!sums)return -1;
    int status=1;
    for(intptr_t f=0;f<nf;f++) {
        intptr_t begin=offsets[f],end=f+1<nf?offsets[f+1]:nc;double area[3]={0.,0.,0.};
        if(begin<0||end>nc||end-begin<3) { status=-1;goto shade_end; }
        for(intptr_t j=begin;j<end;j++) {
            intptr_t ia=vertices[j],ib=next[j];if(ia<0||ia>=nv||ib<0||ib>=nv) { status=-1;goto shade_end; }
            double value[3];cross(points+3*ia,points+3*ib,value);for(int d=0;d<3;d++)area[d]+=value[d];
        }
        if(!smooth) { double size=norm(area);for(int d=0;d<3;d++)area[d]=size>EPS?area[d]/size:0.; }
        for(intptr_t j=begin;j<end;j++) {
            if(smooth) { intptr_t group=groups[j];if(group<0||group>=nc) { status=-1;goto shade_end; }for(int d=0;d<3;d++)sums[3*group+d]+=area[d]; }
            else for(int d=0;d<3;d++)out[3*j+d]=area[d];
        }
    }
    if(smooth) {
        for(intptr_t j=0;j<nc;j++) { double *v=sums+3*j,size=norm(v);for(int d=0;d<3;d++)v[d]=size>EPS?v[d]/size:0.; }
        for(intptr_t j=0;j<nc;j++)for(int d=0;d<3;d++)out[3*j+d]=sums[3*groups[j]+d];
    }
shade_end:
    free(sums);return status;
}
API int nwh_locate(const double *offsets,const double *tables,intptr_t segments,intptr_t subdivisions,double total,const double *u,intptr_t n,intptr_t *out_segment,double *out_t,intptr_t *vertex) {
    if(segments<1||subdivisions<1||n<0||!isfinite(total)||total<=EPS)return -1;
    for(intptr_t k=0;k<n;k++) {
        double value=limit(u[k],0.,1.),distance=value*total;intptr_t i=right(offsets,segments+1,distance)-1;
        if(i<0)i=0;if(i>=segments)i=segments-1;out_segment[k]=i;out_t[k]=0.;vertex[k]=-1;
        if(value==0.) { vertex[k]=0;continue; }
        if(value==1.) { vertex[k]=segments;continue; }
        if(fabs(distance-offsets[i])<1e-10) { vertex[k]=i;continue; }
        if(fabs(distance-total)<1e-10) { vertex[k]=segments;continue; }
        const double *arc=tables+i*(subdivisions+1);double d=distance-offsets[i];intptr_t j=left(arc,subdivisions+1,d);
        if(j<1)j=1;if(j>subdivisions)j=subdivisions;
        out_t[k]=((j-1)+(d-arc[j-1])/fmax(EPS,arc[j]-arc[j-1]))/subdivisions;
    }
    return 1;
}
API int nwh_evaluate(const double *coefficients,const uint8_t *reverse,intptr_t segments,const intptr_t *indices,const double *parameters,intptr_t n,double *out) {
    for(intptr_t k=0;k<n;k++) {
        intptr_t i=indices[k];if(i<0||i>=segments)return -1;
        const double *v=coefficients+12*i;double t=reverse[i]?1.-parameters[k]:parameters[k];
        if(t==0.||t==1.) { for(int d=0;d<3;d++)out[k*3+d]=v[(t==0.?0:3)+d];continue; }
        for(int d=0;d<3;d++) {
            double p=v[d],q=v[d+3],ma=v[d+6],mb=v[d+9];
            double cubic=2*p-2*q+ma+mb,quadratic=-3*p+3*q-2*ma-mb;
            out[k*3+d]=((cubic*t+quadratic)*t+ma)*t+p;
        }
    }
    return 1;
}
